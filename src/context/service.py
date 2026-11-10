import hashlib
import json
from pathlib import Path
from src.identity.store import uid,now
from src.tracking.video import registered_clip,safe_path,sha256
from .ingest import parse
from .repository import tokens
from .temporal import seconds,map_activity,explicit_suggestions
from .knowledge import ACTION_REQUIREMENTS


class ContextService:
    def __init__(self,repo,access,source):self.repo,self.access,self.source=repo,access,source

    def ingest(self,filename,data,title,source_type,actor,is_example=False,scope=None):
        from .material_library import validate_scope,set_scope
        validate_scope(self,actor,scope)
        extracted=parse(filename,data);did=uid();digest=hashlib.sha256(data).hexdigest()
        with self.repo.connection() as c:
            c.execute('INSERT INTO context_documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(did,actor,source_type,title,filename,Path(filename).suffix.lower(),digest,extracted['parser_version'],actor,now(),extracted['status'],int(is_example),1,self.access.seal(data),extracted['canonical_text']))
            for p in extracted['passages']:
                pid=uid();provenance={'parser_version':extracted['parser_version'],'file_hash':digest,'offset_kind':'canonical_extracted_unicode_codepoints','warnings':extracted['warnings']}
                c.execute('INSERT INTO context_passages VALUES (?,?,?,?,?,?,?,?)',(pid,did,p['content'],p['page_or_slide'],p['source_start'],p['source_end'],json.dumps(provenance), 'extracted_not_teacher_verified'))
                c.execute('INSERT INTO context_fts VALUES (?,?)',(pid,' '.join(tokens(p['content']))))
            self.repo.audit(c,did,actor,'document_ingested',{'hash':digest,'status':extracted['status'],'is_example':is_example})
        if scope is not None:set_scope(self,did,actor,scope)
        return {'document_id':did,'status':extracted['status'],'passage_count':len(extracted['passages']),'file_hash':digest,'warnings':extracted['warnings']}

    def detail(self,did,actor):
        d=self.repo.document(did,actor);d.pop('original',None)
        from .material_library import scope_rows
        d['scope']=scope_rows(self).get(did,{})
        with self.repo.connection() as c:
            d['passages']=[dict(r) for r in c.execute('SELECT * FROM context_passages WHERE document_id=? ORDER BY source_start',(did,))]
        d['source_warning']='Project-authored example, not the original classroom teacher.' if d['is_example'] else 'Uploader-declared source; no independent verification of teacher assertions.'
        return d

    def activity(self,body,actor):
        p=self.repo.passage(body.source_passage_id,actor);d=self.repo.document(p['document_id'],actor,True)
        a,b=seconds(body.start,body.time_reference),seconds(body.end,body.time_reference)
        if a is not None and b<a:raise ValueError('activity_end_precedes_start')
        if set(body.action_claims)-set(ACTION_REQUIREMENTS):raise ValueError('unknown_action_class')
        if body.status=='planned' and any(v!='expected' for v in body.action_claims.values()):raise ValueError('plan_cannot_assert_observed_actions')
        if body.status=='teacher_reported_actual' and d['source_type'] not in {'teacher_observation','teacher_note','activity_schedule'}:raise ValueError('plan_source_cannot_be_actual_observation')
        aid=uid()
        with self.repo.connection() as c:
            version=1
            if body.supersedes_activity:
                old=c.execute('SELECT * FROM teaching_activities WHERE activity_id=?',(body.supersedes_activity,)).fetchone()
                if not old or old['document_id']!=p['document_id']:raise ValueError('activity_version_scope_mismatch')
                version=old['version']+1;c.execute('UPDATE teaching_activities SET revoked=1 WHERE activity_id=?',(old['activity_id'],))
            c.execute('INSERT INTO teaching_activities VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(aid,p['document_id'],p['passage_id'],body.activity_type,body.description,a,b,body.time_reference,body.status,version,0,json.dumps(body.action_claims)))
            self.repo.audit(c,aid,actor,'activity_created',body.model_dump())
        return {'activity_id':aid,'status':body.status,'time_reference':body.time_reference}

    def link(self,body,actor):
        self.repo.document(body.document_id,actor,True)
        clip=registered_clip(self.source.registry_db,clip_id=body.clip_id)
        anchor=seconds(body.text_anchor,body.time_reference)
        if anchor is None:raise ValueError('cannot_confirm_unknown_time_mapping')
        if not clip['start']<=body.video_global_anchor<=clip['end']:raise ValueError('video_anchor_outside_registered_clip')
        digest=sha256(safe_path(clip,self.source.video_root));lid=uid();version=1
        with self.repo.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute("SELECT * FROM context_video_links WHERE document_id=? AND clip_id=? AND status='confirmed'",(body.document_id,body.clip_id)).fetchone()
            if old:
                if body.supersedes!=old['link_id']:raise ValueError('existing_mapping_requires_explicit_supersedes')
                version=old['version']+1;c.execute("UPDATE context_video_links SET status='superseded' WHERE link_id=?",(old['link_id'],))
            elif body.supersedes:
                prev=c.execute('SELECT * FROM context_video_links WHERE link_id=?',(body.supersedes,)).fetchone()
                if not prev or prev['document_id']!=body.document_id or prev['clip_id']!=body.clip_id:raise ValueError('superseded_mapping_scope_mismatch')
                version=prev['version']+1
            c.execute('INSERT INTO context_video_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(lid,body.lesson_id,body.document_id,body.clip_id,body.time_reference,anchor,body.video_global_anchor,digest,body.basis,actor,now(),'confirmed',version,body.supersedes))
            self.repo.audit(c,lid,actor,'alignment_confirmed',body.model_dump())
        return {'link_id':lid,'version':version,'status':'confirmed','scope':'Explicit clip-scoped mapping; not proof activity occurred.'}

    def revoke(self,kind,object_id,actor,reason):
        table,key={'activity':('teaching_activities','activity_id'),'link':('context_video_links','link_id')}[kind]
        with self.repo.connection() as c:
            r=c.execute('SELECT * FROM '+table+' WHERE '+key+'=?',(object_id,)).fetchone()
            if not r:raise ValueError('context_record_not_found')
            self.repo.document(r['document_id'],actor,True,c)
            if kind=='activity':c.execute('UPDATE teaching_activities SET revoked=1,version=version+1 WHERE activity_id=?',(object_id,))
            else:c.execute("UPDATE context_video_links SET status='revoked' WHERE link_id=?",(object_id,))
            self.repo.audit(c,object_id,actor,kind+'_revoked',{'reason':reason})
        return {'status':'revoked'}

    def delete(self,did,actor):
        with self.repo.connection() as c:
            self.repo.document(did,actor,True,c)
            c.execute('DELETE FROM context_fts WHERE passage_id IN (SELECT passage_id FROM context_passages WHERE document_id=?)',(did,))
            c.execute("UPDATE context_passages SET content='',extraction_status='deleted' WHERE document_id=?",(did,))
            c.execute("UPDATE context_documents SET original=NULL,canonical_text='',title='[deleted]',filename='',status='deleted',version=version+1 WHERE document_id=?",(did,))
            c.execute('UPDATE teaching_activities SET revoked=1,description=\'\',version=version+1 WHERE document_id=?',(did,))
            c.execute("UPDATE context_video_links SET status='revoked' WHERE document_id=?",(did,))
            # Quoted content may exist in a reader's cached result or edited activity audit.
            c.execute("UPDATE context_analyses SET result='{}',revision=-1 WHERE result LIKE ?",('%'+did+'%',))
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='fusion_reports'").fetchone():
                affected=c.execute('SELECT report_id,owner FROM fusion_reports WHERE payload LIKE ?',('%'+did+'%',)).fetchall()
                for old_report in affected:
                    c.execute('INSERT INTO fusion_report_audit VALUES (?,?,?,?,?,?)',(uid(),old_report['report_id'],actor,'stale_detected',now(),json.dumps({'invalid_dependencies':[{'dependency_id':'document:'+did,'kind':'context_document','reason':'deleted_and_private_content_redacted'}]})))
                c.execute("UPDATE fusion_reports SET payload='{}',status='stale' WHERE payload LIKE ?",('%'+did+'%',))
            c.execute("UPDATE context_audit SET payload='{}' WHERE object_id=? OR object_id IN (SELECT activity_id FROM teaching_activities WHERE document_id=?) OR object_id IN (SELECT link_id FROM context_video_links WHERE document_id=?)",(did,did,did))
            c.execute("UPDATE context_video_links SET basis='[source deleted]' WHERE document_id=?",(did,))
            self.repo.audit(c,did,actor,'document_deleted',{})
        with self.repo.connection() as c:c.execute('VACUUM')
        return {'status':'deleted','dependent_links_revoked':True}

    def activities(self,actor,clip_id=None,lesson_id=None):
        allowed={d['document_id'] for d in self.repo.documents(actor)}
        clip=registered_clip(self.source.registry_db,clip_id=clip_id) if clip_id else None
        with self.repo.connection() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM teaching_activities WHERE revoked=0 ORDER BY start_seconds,activity_id')]
            links=[dict(r) for r in c.execute("SELECT * FROM context_video_links WHERE status='confirmed'" )]
        result=[]
        digest=sha256(safe_path(clip,self.source.video_root)) if clip else None
        for a in rows:
            if a['document_id'] not in allowed:continue
            choices=[l for l in links if l['document_id']==a['document_id'] and (not clip or l['clip_id']==clip_id) and (not lesson_id or l['lesson_id']==lesson_id)]
            if clip and clip.get('source_kind')=='uploaded_recording' and not choices:continue
            if lesson_id and not choices:continue
            l=choices[0] if len(choices)==1 else None
            if l and clip and l['source_sha256']!=digest:l=None
            a['alignment']=map_activity(a,l,clip) if clip else {'status':'alignment_unknown'}
            a['action_claims']=json.loads(a['action_claims']);d=self.repo.document(a['document_id'],actor)
            a.update(source_kind=d['source_type'],is_example=bool(d['is_example']),source_status='project_example' if d['is_example'] else 'uploader_declared',passage_ref=a['source_passage_id'])
            result.append(a)
        return result

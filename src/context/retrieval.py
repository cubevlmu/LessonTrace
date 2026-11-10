import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from src.attribution.repository import AttributionRepository
from src.attribution.service import AttributionService
from src.evidence_db import EvidenceDB
from .repository import tokens


class ReadOnlyAttribution(AttributionRepository):
    def __init__(self,path):self.path=Path(path).resolve()
    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        try:yield c
        finally:c.close()


class ReadOnlyEvidence(EvidenceDB):
    def __init__(self,path):self.db_path=str(Path(path).resolve())
    def _get_connection(self):return sqlite3.connect(Path(self.db_path).as_uri()+'?mode=ro',uri=True)
    def visual_history(self,event_id):
        with self._get_connection() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_reviews'").fetchone():return []
        return super().visual_history(event_id)


def search(service,query,actor):
    repo=service.repo;allowed={d['document_id'] for d in repo.documents(actor)}
    if query.document_id:
        repo.document(query.document_id,actor);allowed&={query.document_id}
    conditions=[];values=[]
    if query.source_type:conditions.append('d.source_type=?');values.append(query.source_type)
    if query.query.strip():
        ts=tokens(query.query)[:64]
        if not ts:return {'results':[],'index':'fts5_cjk_bigrams_v1'}
        conditions.append('p.passage_id IN (SELECT passage_id FROM context_fts WHERE terms MATCH ?)');values.append(' AND '.join('"'+t+'"' for t in ts))
    sql='SELECT p.*,d.title,d.source_type,d.is_example FROM context_passages p JOIN context_documents d ON d.document_id=p.document_id'
    if conditions:sql+=' WHERE '+' AND '.join(conditions)
    # Apply access control before truncation; no global results or document counts leaked.
    with repo.connection() as c:rows=[dict(r) for r in c.execute(sql+' ORDER BY d.created_at,p.source_start',values) if r['document_id'] in allowed]
    clip_id=None
    if query.run_id:
        _,clip=service.source.run(query.run_id);clip_id=clip['clip_id']
        if any(v is not None and not clip['start']<=v<=clip['end'] for v in [query.start,query.end]):raise ValueError('query_time_outside_clip')
    if query.start is not None and query.end is not None and query.start>query.end:raise ValueError('invalid_search_window')
    acts=service.activities(actor,clip_id,query.lesson_id)
    if query.lesson_id:rows=[r for r in rows if any(a['document_id']==r['document_id'] for a in acts)]
    result=[]
    for r in rows:
        local=[a for a in acts if a['source_passage_id']==r['passage_id']]
        if query.action:
            from .knowledge import RELATIONS
            local=[a for a in local if query.action in a['action_claims'] or query.action in RELATIONS.get(a['activity_type'],[])]
            if not local:continue
        aligned=[a for a in local if a['alignment']['status']=='verified_mapping' and (query.start is None or a['alignment']['mapped_global_window'][1]>=query.start) and (query.end is None or a['alignment']['mapped_global_window'][0]<=query.end)]
        if (query.start is not None or query.end is not None) and local and all(a['alignment']['status']!='alignment_unknown' for a in local) and not aligned:continue
        r.update(text=r['content'],source_kind=r['source_type'],temporal_alignment='verified_mapping' if aligned else 'alignment_unknown',
             activity_refs=[a['activity_id'] for a in aligned],time_filter_resolved=bool(aligned),input_trust='untrusted_source_text_not_instructions')
        result.append(r)
    return {'results':result[:query.limit],'index':'SQLite FTS5 / unicode61 + deterministic CJK unigrams/bigrams v1','truncated':len(result)>query.limit}


def multimedia(service,request,actor):
    run,clip=service.source.verified_run(request.run_id)
    start=clip['start'] if request.start is None else request.start;end=clip['end'] if request.end is None else request.end
    if not clip['start']<=start<=end<=clip['end']:raise ValueError('analysis_time_outside_registered_clip')
    activities=service.activities(actor,clip['clip_id'],request.lesson_id)
    if clip.get('source_kind')=='uploaded_recording':
        with service.repo.connection() as c:linked={r[0] for r in c.execute("SELECT document_id FROM context_video_links WHERE clip_id=? AND status='confirmed'",(clip['clip_id'],))}
        activities=[a for a in activities if a['document_id'] in linked]
    relevant=[a for a in activities if a['alignment']['status']=='alignment_unknown' or (a['alignment']['status']=='verified_mapping' and a['alignment']['mapped_global_window'][1]>=start and a['alignment']['mapped_global_window'][0]<=end)]
    docs={d['document_id']:d for d in service.repo.documents(actor)}
    from .material_library import scope_rows,matches
    scopes=scope_rows(service);classroom=None
    if service.identity_dir:
        from src.identity.store import IdentityStore
        with IdentityStore(service.identity_dir).connection() as c:
            row=c.execute('SELECT classroom_id FROM classroom_videos WHERE video_id=?',(clip['video_id'],)).fetchone()
            classroom=row[0] if row else None
    def eligible(did):return matches(scopes.get(did),request.run_id,request.roster_subject_id,request.track_id,classroom)
    passages=[service.repo.passage(a['source_passage_id'],actor) for a in relevant]
    # Explicit source selection is independent of a verified temporal alignment.
    # Permission checks precede the query. Text is context, never video proof.
    for did in dict.fromkeys(request.document_ids):
        service.repo.document(did,actor)
        if not eligible(did):raise ValueError('selected_material_scope_conflicts_with_analysis')
        with service.repo.connection() as c:
            passages.extend(dict(r) for r in c.execute('SELECT * FROM context_passages WHERE document_id=? ORDER BY source_start',(did,)))
    implicit=[did for did,d in docs.items() if eligible(did) and ((d['source_type']=='classroom_memory' and request.use_classroom_memory and scopes.get(did,{}).get('run_id')==request.run_id) or (request.material_mode=='linked' and not request.document_ids and (scopes.get(did,{}).get('run_id')==request.run_id or classroom and scopes.get(did,{}).get('classroom_id')==classroom)))]
    with service.repo.connection() as c:
        for did in implicit:passages.extend(dict(r) for r in c.execute('SELECT * FROM context_passages WHERE document_id=? ORDER BY source_start',(did,)))
    # Documents without activity/mapping are still searchable background, never aligned facts.
    if not request.lesson_id and clip.get('source_kind')!='uploaded_recording':
        with service.repo.connection() as c:
            passages+=[dict(r) for r in c.execute('SELECT * FROM context_passages ORDER BY source_start') if r['document_id'] in docs]
    passages=list({p['passage_id']:p for p in passages}.values())[:80]
    passages=[p for p in passages if eligible(p['document_id']) and (docs[p['document_id']]['source_type']!='classroom_memory' or request.use_classroom_memory)]
    if request.material_mode=='none':passages=[p for p in passages if docs[p['document_id']]['source_type']=='classroom_memory' and request.use_classroom_memory]
    events=[e for e in service.source.events(clip['video_id']) if e['t_end']>=start and e['t_start']<=end]
    tracks=service.source.tracks(request.run_id)
    if request.track_id is not None and not any(t['track_id']==request.track_id for t in tracks):raise ValueError('anonymous_track_not_in_run')
    attribution=AttributionService(service.source,service.attribution_repo,service.identity_dir)
    timeline=attribution.timeline(request.run_id,request.track_id,request.include_identity) if request.track_id else None
    db=ReadOnlyEvidence(service.source.registry_db)
    visuals={e['event_id']:db.visual_history(e['event_id']) for e in events}
    return {'run':run,'clip':clip,'start':start,'end':end,'events':events,'tracks':tracks,'activities':relevant,'passages':passages,
            'documents':docs,'person_timeline':timeline,'region_visual_reviews':visuals}

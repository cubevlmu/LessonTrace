"""Independent, fixed-format editable reports backed by current fusion evidence, never chat state."""
import json
from typing import Literal
from fastapi import Depends,Response,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field,model_validator
from src.identity.store import uid,now
from src.agent.fusion import reporting
from src.agent.fusion.planning import ReviewRequest,make_scope_key
from src.agent.fusion.structured import section_for
from .report_blocks import edit_blocks,edit_sections,ensure_blocks,project,section_block
from .report_adoptions import (
    answer_content,
    append_adoption,
    complete_first_registry,
    evidence_index,
    find_adoption,
    insert_version,
    mismatch_reasons,
    restore_section_refs,
    result as adoption_result,
    serialized,
    source_reasons,
)
from .report_sources import MISSING_REF,adoption_check_labels,escape_markdown,source_labels

DISPLAY_FIELDS={'report_id','version','origin','created_at','status','options','reason','source_labels','adoption_check_labels','visible_sections'}

TEMPLATES={
 'lesson':[('scope','课堂基本信息与分析范围'),('materials','教学背景与资料依据'),('observations','课堂可观察表现'),('analysis','教学任务与课堂组织分析'),('recommendations','教学改进与实施步骤'),('followup','后续跟进与核对'),('limits','覆盖限制与证据边界')],
 'students':[('scope','群体观察范围'),('people','人物区域与身份情况'),('observations','已观察行为与时序分布'),('materials','任务和作业背景'),('analysis','群体共性与差异'),('recommendations','面向全体的教学支持'),('followup','分层跟进与核对'),('limits','未覆盖人群与时段')],
 'student':[('scope','人物范围与身份来源'),('observations','个人外显动作时间线'),('materials','个人作业与教学任务材料'),('analysis','行为与任务的对照分析'),('recommendations','个别反馈建议'),('followup','下一步支持与核对'),('limits','个人分析的适用边界')]
}
class Generate(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    run_id:str|None=None
    angle:Literal['lesson','students','student']='lesson'
    roster_subject_id:str|None=None
    subject_id:str|None=None
    track_id:int|None=Field(None,ge=1)
    lesson_id:str|None=None
    start:float|None=None
    end:float|None=None
    document_ids:list[str]=Field(default_factory=list,max_length=10)
    material_mode:Literal['linked','selected','none']='linked'
    use_classroom_memory:bool=True
    focus:str=Field('',max_length=1000)
    use_llm:bool=True
    @model_validator(mode='after')
    def person(self):
        make_scope_key(self.lesson_id,self.run_id)
        if not self.run_id and (self.track_id or self.start is not None or self.end is not None):
            raise ValueError('video_scope_requires_run')
        has_person=bool(self.roster_subject_id or self.track_id or self.subject_id)
        if self.angle=='student' and not has_person:raise ValueError('student_report_requires_selected_person')
        if self.angle!='student' and has_person:raise ValueError('whole_class_report_cannot_inherit_single_person_filter')
        return self
class CreateFromAnswer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['from_answer']
    fusion_report_id:str=Field(min_length=1)
    create_reason:Literal['first_draft','scope_mismatch']
    target_report_id:str|None=None
    @model_validator(mode='after')
    def validate_target(self):
        if self.create_reason=='scope_mismatch' and not self.target_report_id:raise ValueError('target_report_id_required_for_scope_mismatch')
        if self.create_reason=='first_draft' and self.target_report_id is not None:raise ValueError('target_report_id_not_allowed_for_first_draft')
        return self
class Adopt(BaseModel):
    model_config=ConfigDict(extra='forbid')
    fusion_report_id:str=Field(min_length=1)
    expected_version:int=Field(ge=1)
class Section(BaseModel):
    model_config=ConfigDict(extra='forbid')
    section_id:str
    heading:str=Field(min_length=1,max_length=100)
    content:str=Field(max_length=10000)
    evidence_refs:list[str]=Field(default_factory=list,max_length=200)
class BlockEdit(BaseModel):
    # Extra fields from a saved block are read-only and never trusted on input.
    block_id:str|None=None
    section_id:str|None=None
    text:str=Field(max_length=10000)
    evidence_refs:list[str]|None=Field(None,max_length=200)
class Edit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    sections:list[Section]|None=Field(None,min_length=1,max_length=10)
    blocks:list[BlockEdit]|None=Field(None,min_length=1,max_length=100)
    @model_validator(mode='after')
    def validate_changes(self):
        if self.sections is None and self.blocks is None:
            raise ValueError('report_edit_requires_sections_or_blocks')
        return self
class Revise(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    instruction:str=Field(min_length=1,max_length=1000)
    keep_teacher_edits:bool=True

def section_document(report,options):
    groups={};known={e['evidence_id'] for e in report['evidence']}
    visible={e['provenance'].get('track_id') for e in report['evidence'] if e['evidence_type']=='person_track'}
    observed={e['provenance'].get('track_id') for e in report['evidence'] if e['evidence_type'] in {'vlm_observation','model_discovered_event','human_confirmed_event'} and e['provenance'].get('track_id') is not None}
    coverage={'visible_track_records':len(visible),'tracks_with_behavior_evidence':len(observed),'not_student_count':True}
    for p in report.get('narrative',[]) if 'statements' not in report else []:
        h=p['heading'];kind='analysis' if h.startswith(('AI分析','AI综合')) or h in {'综合分析','依据与限制'} else 'recommendations' if h.startswith('教学建议') or h=='教师建议' else 'observations' if h=='可观察行为' else 'materials' if h=='结合教学与学习资料' else 'scope' if h=='本次复盘范围' else 'limits'
        groups.setdefault(kind,[]).append(p)
    for statement in report.get('statements',[]):
        groups.setdefault(section_for(statement),[]).append(statement)
    sections=[]
    for key,title in TEMPLATES[options.angle]:
        parts=groups.get(key,[]);text='\n\n'.join(p['text'] for p in parts);refs=list(dict.fromkeys(r for p in parts for r in p['evidence_refs'] if r in known))
        if key=='people' and 'statements' not in report:
            tracks=[e for e in report['evidence'] if e['evidence_type']=='person_track'];text=f'本次范围检索到{len(tracks)}条人物轨迹记录。轨迹数量不等于学生人数；局部、遮挡、重复候选及身份未确认的情况分别保留。';refs=[e['evidence_id'] for e in tracks]
        if key=='scope' and options.angle!='student' and 'statements' not in report:text+=f'\n\n本次可引用的区域轨迹记录{len(visible)}条，其中有行为取证记录的轨迹{len(observed)}条；轨迹不是学生人数，未分析对象不计入行为结论。'
        sections.append({'section_id':key,'heading':title,'content':text,'evidence_refs':refs})
    if report.get('statements'):
        # Current compatibility sections mirror these canonical model blocks.
        valid_sections={s['section_id'] for s in sections}
        blocks=[section_block({'section_id':section_for(p) if section_for(p) in valid_sections else 'analysis',
                               'content':p['text'],'evidence_refs':[r for r in p['evidence_refs'] if r in known]},
                              1,None,statement_id=p['statement_id']) for p in report['statements']]
    else:
        blocks=[section_block(section,1,None) for section in sections if section['content'].strip()]
    return {'blocks':blocks,'title':{'lesson':'课堂整体复盘报告','students':'全体学生观察报告','student':'学生个人复盘报告'}[options.angle],'angle':options.angle,'sections':sections,'adoptions':[],'analysis_coverage':coverage,'evidence':report['evidence'],'classroom':report['classroom'],'product_scope':report.get('product_scope'),'generation':report.get('narrative_generation'),'scope_warning':'仅覆盖实际分析范围，不能将未分析的人物与时段计入统计。','source_kind':'model_assisted_report_not_human_behavior_verification'}

def mount(app,service,workflow,account,writer):
    with service.repo.connection() as c:c.executescript('''CREATE TABLE IF NOT EXISTS lesson_reports(report_id TEXT PRIMARY KEY,owner TEXT NOT NULL,run_id TEXT NOT NULL,angle TEXT NOT NULL,options TEXT NOT NULL,created_at TEXT NOT NULL);CREATE TABLE IF NOT EXISTS lesson_report_versions(report_id TEXT NOT NULL REFERENCES lesson_reports(report_id),version INTEGER NOT NULL,base_fusion_id TEXT NOT NULL,origin TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,instruction TEXT NOT NULL,PRIMARY KEY(report_id,version));''')
    with service.repo.connection() as c:
        if 'scope_key' not in {r['name'] for r in c.execute('PRAGMA table_info(lesson_reports)')}:
            c.execute("ALTER TABLE lesson_reports ADD COLUMN scope_key TEXT")
        c.execute('''UPDATE lesson_reports SET scope_key=CASE
            WHEN json_extract(options,'$.lesson_id') IS NOT NULL AND json_extract(options,'$.lesson_id')!=''
                THEN 'lesson:'||json_extract(options,'$.lesson_id') ELSE 'run:'||run_id END WHERE scope_key IS NULL''')
        c.execute('CREATE INDEX IF NOT EXISTS lesson_report_scope ON lesson_reports(owner,scope_key,created_at)')
    def add_draft(c,rid,options,actor):
        c.execute('''INSERT INTO lesson_reports
            (report_id,owner,run_id,angle,options,created_at,scope_key) VALUES(?,?,?,?,?,?,?)''',
            (rid,actor,options.run_id or '',options.angle,options.model_dump_json(),now(),
             make_scope_key(options.lesson_id,options.run_id)))
    def is_privileged(a):return a['role'] in {'admin','teacher','reviewer'}
    def stored(rid,actor):
        with service.repo.connection() as c:
            r=c.execute('SELECT * FROM lesson_reports WHERE report_id=? AND owner=?',(rid,actor)).fetchone()
            v=c.execute('SELECT * FROM lesson_report_versions WHERE report_id=? ORDER BY version DESC LIMIT 1',(rid,)).fetchone() if r else None
        if not r or not v:raise PermissionError('lesson_report_access_denied')
        return dict(r),dict(v)
    def verify_adoptions_current(doc,a):
        privileged=is_privileged(a)
        for adoption in doc.get('adoptions',[]):
            fid=adoption.get('fusion_report_id')
            if not fid:return 'adopted_answer_source_invalid'
            try:
                adopted_answer=reporting.load(service,fid,a['actor'],privileged)
            except (PermissionError,ValueError):
                return 'adopted_answer_source_revoked'
            if adopted_answer['status']!='current':
                return adopted_answer.get('reason') or 'adopted_answer_source_stale'
        return None
    def load(rid,a,record=None):
        r,v=record if record is not None else stored(rid,a['actor'])
        base=reporting.load(service,v['base_fusion_id'],a['actor'],is_privileged(a))
        doc=json.loads(v['payload']);doc.setdefault('adoptions',[])
        ensure_blocks(doc,v['version'],v['actor'])
        project(doc)
        doc.update(report_id=rid,version=v['version'],origin=v['origin'],created_at=v['created_at'],status=base['status'],options=json.loads(r['options']))
        stale_reason=base.get('reason') if base['status']!='current' else verify_adoptions_current(doc,a)
        if stale_reason is not None:
            doc.update(status='stale',sections=[],evidence=[],adoptions=[],blocks=[],visible_sections=[],source_labels={},adoption_check_labels={},reason=stale_reason)
        else:
            doc['source_labels']=source_labels(service,base,doc,a['actor'])
            doc['adoption_check_labels']=adoption_check_labels(service,base,doc,a['actor'])
        return doc
    def ask_request(options,draft=''):
        return {'run_id':options.run_id,'question':'请按固定报告范式生成完整复盘，结合所有所选资料；'+options.focus,'report_template':options.angle,'report_context':draft,'roster_subject_id':options.roster_subject_id,'subject_id':options.subject_id,'track_id':options.track_id,'lesson_id':options.lesson_id,'start':options.start,'end':options.end,'document_ids':options.document_ids,'material_mode':options.material_mode,'use_classroom_memory':options.use_classroom_memory,'use_llm':options.use_llm,'allow_verification':False,'budget_seconds':180,'max_model_calls':2}
    def generate(options,a,prior=None,draft='',expected_version=None,preserved=None,preserved_adoptions=None):
        result=workflow.ask(ask_request(options,draft),a['actor'],is_privileged(a))
        if result['status']!='current':raise ValueError('report_sources_changed_during_generation')
        doc=section_document(result,options);rid=prior or uid()
        if preserved_adoptions:
            for adoption in preserved_adoptions:
                complete_first_registry(doc,result,adoption)
            registry=evidence_index(doc)
            for adoption in preserved_adoptions:
                if source_reasons(adoption.get('source_versions',{}),registry):
                    raise HTTPException(409,'revise_cannot_preserve_adopted_sources')
            doc['adoptions']=preserved_adoptions
        if preserved:
            known={e['evidence_id'] for e in doc['evidence']}
            if any(not set(b['evidence_refs']) <= known for b in preserved):
                # Some preserved sources are outside the display projection but still in the authorized retrieval.
                authorized=evidence_index(result,retrieved=True)
                for ref in {r for b in preserved for r in b['evidence_refs']} - known:
                    versions=authorized.get(ref,{})
                    if len(versions)!=1:
                        raise HTTPException(409,'revise_cannot_preserve_edited_sources')
                    doc['evidence'].append(next(iter(versions.values())))
            sections={b['section_id'] for b in preserved if not b.get('fusion_report_id')}
            doc['blocks']=[b for b in doc['blocks'] if b['section_id'] not in sections]
            doc['teacher_edited_sections']=list(sections)
        def validate():
            base=reporting.load(service,result['report_id'],a['actor'],is_privileged(a))
            if base['status']!='current':raise HTTPException(409,'report_sources_stale')
            if prior:
                row,previous=stored(prior,a['actor'])
                if previous['version']!=expected_version:
                    raise HTTPException(409,'report_edited_during_generation; reload_current_version')
                if load(prior,a,(row,previous))['status']!='current':
                    raise HTTPException(409,'report_sources_stale')
        def publish(c,_):
            if not prior:add_draft(c,rid,options,a['actor'])
            version=(expected_version or 0)+1
            for block in doc['blocks']:
                block.update(version=version,actor=a['actor'])
            if preserved:
                doc['blocks'].extend(preserved)
            project(doc)
            doc.pop('visible_sections',None)
            # Run-only v1 drafts retain their section-authoritative storage
            # format. The read adapter supplies blocks for those legacy rows.
            if not options.lesson_id and not prior:
                doc.pop('blocks',None)
            insert_version(c,rid,version,result['report_id'],'model_generated',a['actor'],doc,options.focus)
        serialized(service.repo,validate,publish)
        return load(rid,a)
    def scan_drafts(scope_key,a,dedup_key):
        with service.repo.connection() as c:
            rows=c.execute('SELECT report_id FROM lesson_reports WHERE scope_key=? AND owner=? ORDER BY created_at DESC,rowid DESC',(scope_key,a['actor'])).fetchall()
        first_current=None
        for row in rows:
            rid=row['report_id']
            with service.repo.connection() as c:
                v1=c.execute('SELECT payload FROM lesson_report_versions WHERE report_id=? AND version=1',(rid,)).fetchone()
            loaded=load(rid,a)
            if loaded['status']!='current':continue
            if first_current is None:first_current=rid
            if v1 and json.loads(v1['payload']).get('creation_dedup_key')==dedup_key:
                existing=find_adoption(loaded,dedup_key['fusion_report_id'])
                if existing:return ('dedup',rid,loaded['version'],existing)
        if first_current is not None:return ('exists',first_current,None,None)
        return ('none',None,None,None)
    def create_from_answer(body:CreateFromAnswer,a,response:Response):
        answer=reporting.load(service,body.fusion_report_id,a['actor'],is_privileged(a))
        if answer['status']!='current':raise HTTPException(409,'answer_sources_stale')
        content=answer_content(answer)
        plan=answer['query_plan']
        scope_key=make_scope_key(plan.get('lesson_id'),plan.get('run_id'))
        if body.create_reason=='scope_mismatch':
            stored(body.target_report_id,a['actor'])
        dedup_key=(
            {'owner':a['actor'],('run_id' if scope_key.startswith('run:') else 'scope_key'):(plan['run_id'] if scope_key.startswith('run:') else scope_key),'create_reason':'first_draft','fusion_report_id':body.fusion_report_id}
            if body.create_reason=='first_draft'
            else {'owner':a['actor'],'target_report_id':body.target_report_id,'create_reason':'scope_mismatch','fusion_report_id':body.fusion_report_id}
        )
        state,existing_rid,existing_ver,existing_adoption=scan_drafts(scope_key,a,dedup_key)
        if state=='dedup':
            response.status_code=200
            return adoption_result(existing_rid,existing_ver,existing_adoption,duplicate=True,created=True)
        if body.create_reason=='first_draft' and state=='exists':
            raise HTTPException(409,{'code':'draft_exists','run_id':plan['run_id'],'report_id':existing_rid,'lesson_id':plan.get('lesson_id'),'message':'当前范围已有可用报告草稿，请先选择目标草稿。'})
        has_person=bool(plan.get('subject_id') or plan.get('roster_subject_id') or plan.get('track_id'))
        selected_docs=list(plan.get('document_ids') or plan.get('selected_document_ids') or [])
        material_mode=plan.get('material_mode') or ('selected' if selected_docs else 'linked')
        if material_mode not in {'linked','selected','none'}:material_mode='selected' if selected_docs else 'linked'
        bounds=plan.get('effective_time_range') or [None,None]
        options=Generate(
            run_id=plan['run_id'],
            angle='student' if has_person else 'lesson',
            roster_subject_id=plan.get('roster_subject_id'),
            subject_id=plan.get('subject_id'),
            track_id=plan.get('track_id'),
            lesson_id=plan.get('lesson_id'),
            start=bounds[0],
            end=bounds[1],
            document_ids=selected_docs,
            material_mode=material_mode,
            use_classroom_memory=plan.get('use_classroom_memory',True),
            focus='',
            use_llm=False,
        )
        base=workflow.ask(ask_request(options),a['actor'],is_privileged(a))
        if base['status']!='current':raise HTTPException(409,'report_sources_changed_during_generation')
        doc=section_document(base,options)
        complete_first_registry(doc,base,content)
        reasons=mismatch_reasons(service,base,answer,content,doc,a['actor'])
        if reasons:
            raise HTTPException(409,{'code':'scope_mismatch','report_id':'','current_version':0,'fusion_report_id':body.fusion_report_id,'reasons':reasons,'can_create_from_answer':False})
        doc['creation_dedup_key']=dedup_key
        for block in doc['blocks']:
            block['actor']=a['actor']
        adoption=append_adoption(doc,body.fusion_report_id,1,a['actor'],content)
        def validate():
            current_answer=reporting.load(service,body.fusion_report_id,a['actor'],is_privileged(a))
            if current_answer['status']!='current':raise HTTPException(409,'answer_sources_stale')
            current_base=reporting.load(service,base['report_id'],a['actor'],is_privileged(a))
            if current_base['status']!='current':raise HTTPException(409,'report_sources_stale')
            return scan_drafts(scope_key,a,dedup_key)
        def write(c,checked):
            st,erid,ever,eadopt=checked
            if st=='dedup':
                return 200,adoption_result(erid,ever,eadopt,duplicate=True,created=True)
            if body.create_reason=='first_draft' and st=='exists':
                raise HTTPException(409,{'code':'draft_exists','run_id':plan['run_id'],'report_id':erid,'lesson_id':plan.get('lesson_id'),'message':'当前范围已有可用报告草稿，请先选择目标草稿。'})
            rid=uid()
            add_draft(c,rid,options,a['actor'])
            insert_version(c,rid,1,base['report_id'],'teacher_adopted',a['actor'],doc,'Teacher created report draft from assistant answer.')
            return 201,adoption_result(rid,1,adoption,duplicate=False,created=True)
        code,payload=serialized(service.repo,validate,write)
        response.status_code=code
        return payload
    @app.post('/api/context/lesson-reports',status_code=201)
    def new(body:Generate|CreateFromAnswer,response:Response,a=Depends(writer)):
        if isinstance(body,CreateFromAnswer):return create_from_answer(body,a,response)
        return generate(body,a)
    @app.post('/api/context/lesson-reports/{rid}/adoptions',status_code=201)
    def adopt(rid:str,body:Adopt,response:Response,a=Depends(writer)):
        def validate():
            r,v=stored(rid,a['actor'])
            loaded=load(rid,a,(r,v))
            if loaded['status']!='current':raise HTTPException(409,'report_sources_stale')
            base=reporting.load(service,v['base_fusion_id'],a['actor'],is_privileged(a))
            answer=reporting.load(service,body.fusion_report_id,a['actor'],is_privileged(a))
            if answer['status']!='current':raise HTTPException(409,'answer_sources_stale')
            content=answer_content(answer)
            doc=json.loads(v['payload']);doc.setdefault('adoptions',[])
            ensure_blocks(doc,v['version'],v['actor'])
            if content['scope'].get('scope_kind')=='lesson_text':
                complete_first_registry(doc,base,content)
            existing=find_adoption(doc,body.fusion_report_id)
            if existing:return ('duplicate',v['version'],existing)
            if body.expected_version!=v['version']:
                raise HTTPException(409,'report_version_changed; reload_current_version')
            reasons=mismatch_reasons(service,base,answer,content,doc,a['actor'])
            if reasons:
                raise HTTPException(409,{'code':'scope_mismatch','report_id':rid,'current_version':v['version'],'fusion_report_id':body.fusion_report_id,'reasons':reasons,'can_create_from_answer':True})
            return ('adopt',v,doc,content)
        def write(c,checked):
            if checked[0]=='duplicate':
                return 200,adoption_result(rid,checked[1],checked[2],duplicate=True)
            _,v,doc,content=checked
            new_version=v['version']+1
            adoption=append_adoption(doc,body.fusion_report_id,new_version,a['actor'],content)
            insert_version(c,rid,new_version,v['base_fusion_id'],'teacher_adopted',a['actor'],doc,'Teacher adopted assistant answer into report draft.')
            return 201,adoption_result(rid,new_version,adoption,duplicate=False)
        code,payload=serialized(service.repo,validate,write)
        response.status_code=code
        return payload
    @app.get('/api/context/lesson-reports')
    def listing(run_id:str|None=None,lesson_id:str|None=None,a=Depends(account)):
        if lesson_id:
            from .lesson_graph import lesson_index
            lesson_index(service,lesson_id,a['actor'])
            field,value='scope_key',make_scope_key(lesson_id,run_id)
        elif run_id:
            field,value='run_id',run_id
        else:
            raise HTTPException(422,'lesson_id_or_run_id_required')
        with service.repo.connection() as c:
            rows=c.execute(f'SELECT report_id,angle,created_at FROM lesson_reports WHERE {field}=? AND owner=? ORDER BY created_at DESC,rowid DESC',
                           (value,a['actor'])).fetchall()
        return [dict(r) for r in rows]
    @app.get('/api/context/lesson-reports/{rid}')
    def get(rid:str,a=Depends(account)):return load(rid,a)
    @app.put('/api/context/lesson-reports/{rid}')
    def edit(rid:str,body:Edit,a=Depends(writer)):
        def validate():
            r,v=stored(rid,a['actor']);doc=load(rid,a,(r,v))
            if doc['status']!='current':raise HTTPException(409,'report_sources_stale')
            if body.version!=v['version']:raise HTTPException(409,'report_version_changed; reload_before_editing')
            version=body.version+1
            if body.sections is not None:
                if [s.section_id for s in body.sections]!=[s['section_id'] for s in doc['sections']]:
                    raise ValueError('report_template_sections_must_be_preserved')
                known={e['evidence_id'] for e in doc['evidence']}
                if any(not set(s.evidence_refs)<=known for s in body.sections):
                    raise ValueError('edited_report_unknown_evidence_reference')
            if body.blocks is not None:
                edit_blocks(doc,body.blocks,version,a['actor'])
            if body.sections is not None:
                edit_sections(doc,[s.model_dump() for s in body.sections],version,a['actor'])
            project(doc)
            doc['source_kind']='teacher_edited_report_not_automatic_behavior_confirmation'
            return v['base_fusion_id'],{k:value for k,value in doc.items() if k not in DISPLAY_FIELDS}
        def write(c,checked):
            base_id,doc=checked
            insert_version(c,rid,body.version+1,base_id,'teacher_edited',a['actor'],doc,
                           'Teacher edited report text; original model and behavior records retained.')
        serialized(service.repo,validate,write)
        return load(rid,a)

    @app.post('/api/context/lesson-reports/{rid}/revise',status_code=201)
    def revise(rid:str,body:Revise,a=Depends(writer)):
        r,v=stored(rid,a['actor'])
        previous=load(rid,a,(r,v))
        if previous['status']!='current':raise HTTPException(409,'report_sources_stale')
        if body.version!=v['version']:raise HTTPException(409,'report_version_changed')
        options=Generate.model_validate_json(r['options']).model_copy(update={'focus':body.instruction})
        current_refs={e['evidence_id'] for e in previous['evidence']}
        current=[b for section in previous['visible_sections'] for b in section['blocks']
                 if set(b['evidence_refs'])<=current_refs]
        draft=json.dumps([{'section_id':b['section_id'],'text':b['text']} for b in current],ensure_ascii=False)[:16000]
        preserved=[b for b in current if b.get('fusion_report_id') or
                   (body.keep_teacher_edits and b['origin']=='teacher_edited')]
        # Preserve a teacher's intentional deletion (a blank block) as well.
        preserved.extend(b for b in previous['blocks'] if not b['text'].strip() and
                         (b.get('fusion_report_id') or (body.keep_teacher_edits and b['origin']=='teacher_edited')))
        return generate(options,a,rid,draft,body.version,preserved,previous.get('adoptions',[]))

    @app.get('/api/context/lesson-reports/{rid}/recoverable-blocks')
    def recoverable_blocks(rid:str,a=Depends(writer)):
        r,v=stored(rid,a['actor'])
        options=Generate.model_validate_json(r['options'])
        if options.lesson_id:
            from src.agent.fusion.lesson_evidence import index_for
            index_for(service,options,a['actor'])
        elif options.run_id:
            from src.behavior_discovery.ingest import authorize_run
            authorize_run(service,options.run_id,a['actor'],a['role'])
        state=load(rid,a,(r,v))
        doc=json.loads(v['payload'])
        ensure_blocks(doc,v['version'],v['actor'])
        saved=evidence_index(doc)
        registries={}
        def registry(fid):
            if fid not in registries:
                with service.repo.connection() as c:
                    row=c.execute('SELECT request,payload FROM fusion_reports WHERE report_id=? AND owner=?',
                                  (fid,a['actor'])).fetchone()
                try:
                    payload=json.loads(row['payload'])
                    _,fresh=reporting.current_inputs(service,ReviewRequest.model_validate_json(row['request']),
                        a['actor'],is_privileged(a),lesson_graph='lesson_evidence' in payload)
                    registries[fid]=evidence_index(fresh)
                except (PermissionError,ValueError,OSError,TypeError):
                    registries[fid]={}
            return registries[fid]
        blocks=[b for b in doc['blocks'] if b['origin']=='teacher_edited' and b['text'].strip()
                and all(ref in saved and set(saved[ref])<=set(registry(b.get('fusion_report_id') or
                                  v['base_fusion_id']).get(ref,{})) for ref in b['evidence_refs'])]
        return {'report_id':rid,'version':v['version'],'status':state['status'],'blocks':blocks}

    @app.get('/api/context/lesson-reports/{rid}/history')
    def history(rid:str,a=Depends(account)):
        stored(rid,a['actor'])
        with service.repo.connection() as c:return [dict(r) for r in c.execute('SELECT version,origin,actor,created_at,instruction FROM lesson_report_versions WHERE report_id=? ORDER BY version',(rid,))]
    @app.get('/api/context/lesson-reports/{rid}/export/{format}')
    def export(rid:str,format:str,version:int|None=Query(None,ge=1),a=Depends(account)):
        doc=load(rid,a)
        if doc['status']!='current':raise HTTPException(409,'report_sources_stale')
        if version is not None and (doc['version']!=version or stored(rid,a['actor'])[1]['version']!=version):
            raise HTTPException(409,'report_version_changed')
        if format=='json':text=json.dumps(doc,ensure_ascii=False,indent=2);mime='application/json'
        elif format=='markdown':
            blocks=['# '+escape_markdown(doc['title']),doc['scope_warning']]
            check_labels=doc.get('adoption_check_labels') or {}
            def citations(refs):
                return '\n'.join('- '+escape_markdown(doc['source_labels'].get(ref,MISSING_REF)) for ref in refs)
            for section in doc['visible_sections']:
                blocks.append('## '+escape_markdown(section['heading']))
                model_refs=[]
                for block in section['blocks']:
                    if block['origin']=='teacher_edited':
                        blocks.extend([f"### 教师修订（第{block['version']}版） · {escape_markdown(block['actor'])}",
                                       block['text']])
                        if block['evidence_refs']:
                            blocks.extend(['原参考来源（正文已由教师修订）',citations(block['evidence_refs'])])
                    elif block['origin']=='adopted':
                        subject=block.get('subject_label')
                        blocks.extend(['### 教师采纳的助手观察与建议'+(f'（{escape_markdown(subject)}）' if subject else ''),
                                       block['text']])
                        if block['evidence_refs']:
                            blocks.extend(['采纳段依据：',citations(block['evidence_refs'])])
                        fid=block.get('fusion_report_id')
                        if block.get('check_scope') and fid in check_labels:
                            blocks.append('建议核查的录像：'+escape_markdown(check_labels[fid]))
                        if block.get('check_evidence_refs'):
                            blocks.extend(['建议核查的教案：',citations(block['check_evidence_refs'])])
                    else:
                        blocks.append(block['text'])
                        model_refs.extend(block['evidence_refs'])
                if model_refs:blocks.extend(['来源：',citations(dict.fromkeys(model_refs))])
            text='\n\n'.join(blocks)+'\n';mime='text/markdown'
        else:raise ValueError('unsupported_report_format')
        return Response(text,media_type=mime,headers={'Content-Disposition':f'attachment; filename="lesson-report-{rid}.{"md" if format=="markdown" else "json"}"'})

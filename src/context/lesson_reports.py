"""Independent, fixed-format editable reports backed by current fusion evidence, never chat state."""
import json
from typing import Literal
from fastapi import Depends,Response,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from src.identity.store import uid,now
from src.agent.fusion import reporting

TEMPLATES={
 'lesson':[('scope','课堂基本信息与分析范围'),('materials','教学背景与资料依据'),('observations','课堂可观察表现'),('analysis','教学任务与课堂组织分析'),('recommendations','教学改进与实施步骤'),('followup','后续跟进与核对'),('limits','覆盖限制与证据边界')],
 'students':[('scope','群体观察范围'),('people','人物区域与身份情况'),('observations','已观察行为与时序分布'),('materials','任务和作业背景'),('analysis','群体共性与差异'),('recommendations','面向全体的教学支持'),('followup','分层跟进与核对'),('limits','未覆盖人群与时段')],
 'student':[('scope','人物范围与身份来源'),('observations','个人外显动作时间线'),('materials','个人作业与教学任务材料'),('analysis','行为与任务的对照分析'),('recommendations','个别反馈建议'),('followup','下一步支持与核对'),('limits','个人分析的适用边界')]
}
class Generate(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    run_id:str
    angle:Literal['lesson','students','student']='lesson'
    roster_subject_id:str|None=None
    track_id:int|None=Field(None,ge=1)
    start:float|None=None
    end:float|None=None
    document_ids:list[str]=Field(default_factory=list,max_length=10)
    material_mode:Literal['linked','selected','none']='linked'
    focus:str=Field('',max_length=1000)
    use_llm:bool=True
    @model_validator(mode='after')
    def person(self):
        if self.angle=='student' and not(self.roster_subject_id or self.track_id):raise ValueError('student_report_requires_selected_person')
        if self.angle!='student' and (self.roster_subject_id or self.track_id):raise ValueError('whole_class_report_cannot_inherit_single_person_filter')
        return self
class Section(BaseModel):
    model_config=ConfigDict(extra='forbid')
    section_id:str
    heading:str=Field(min_length=1,max_length=100)
    content:str=Field(max_length=10000)
    evidence_refs:list[str]=Field(default_factory=list,max_length=200)
class Edit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    sections:list[Section]=Field(min_length=1,max_length=10)
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
    for p in report.get('narrative',[]):
        h=p['heading'];kind='analysis' if h.startswith(('AI分析','AI综合')) or h in {'综合分析','依据与限制'} else 'recommendations' if h.startswith('教学建议') or h=='教师建议' else 'observations' if h=='可观察行为' else 'materials' if h=='结合教学与学习资料' else 'scope' if h=='本次复盘范围' else 'limits'
        groups.setdefault(kind,[]).append(p)
    sections=[]
    for key,title in TEMPLATES[options.angle]:
        parts=groups.get(key,[]);text='\n\n'.join(p['text'] for p in parts);refs=list(dict.fromkeys(r for p in parts for r in p['evidence_refs'] if r in known))
        if key=='people':
            tracks=[e for e in report['evidence'] if e['evidence_type']=='person_track'];text=f'本次范围检索到{len(tracks)}条人物轨迹记录。轨迹数量不等于学生人数；局部、遮挡、重复候选及身份未确认的情况分别保留。';refs=[e['evidence_id'] for e in tracks]
        if key=='followup':text='建议先核对代表性画面及实际作业，再记录教师反馈。具体步骤参见教学建议；完成后重新生成报告，可更新结论依据。'
        if key=='scope' and options.angle!='student':text+=f'\n\n本次可引用的区域轨迹记录{len(visible)}条，其中有行为取证记录的轨迹{len(observed)}条；轨迹不是学生人数，未分析对象不计入行为结论。'
        if not text:text='当前所选范围暂无可支撑本节分析的独立资料；本节保留为空缺项，不编造课堂事实。'
        sections.append({'section_id':key,'heading':title,'content':text,'evidence_refs':refs})
    return {'title':{'lesson':'课堂整体复盘报告','students':'全体学生观察报告','student':'学生个人复盘报告'}[options.angle],'angle':options.angle,'sections':sections,'analysis_coverage':coverage,'evidence':report['evidence'],'classroom':report['classroom'],'product_scope':report.get('product_scope'),'generation':report.get('narrative_generation'),'scope_warning':'仅覆盖实际分析范围，不能将未分析的人物与时段计入统计。','source_kind':'model_assisted_report_not_human_behavior_verification'}

def mount(app,service,workflow,account,writer):
    with service.repo.connection() as c:c.executescript('''CREATE TABLE IF NOT EXISTS lesson_reports(report_id TEXT PRIMARY KEY,owner TEXT NOT NULL,run_id TEXT NOT NULL,angle TEXT NOT NULL,options TEXT NOT NULL,created_at TEXT NOT NULL);CREATE TABLE IF NOT EXISTS lesson_report_versions(report_id TEXT NOT NULL REFERENCES lesson_reports(report_id),version INTEGER NOT NULL,base_fusion_id TEXT NOT NULL,origin TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,instruction TEXT NOT NULL,PRIMARY KEY(report_id,version));''')
    def stored(rid,actor):
        with service.repo.connection() as c:
            r=c.execute('SELECT * FROM lesson_reports WHERE report_id=? AND owner=?',(rid,actor)).fetchone()
            v=c.execute('SELECT * FROM lesson_report_versions WHERE report_id=? ORDER BY version DESC LIMIT 1',(rid,)).fetchone() if r else None
        if not r or not v:raise PermissionError('lesson_report_access_denied')
        return dict(r),dict(v)
    def load(rid,a):
        r,v=stored(rid,a['actor']);base=reporting.load(service,v['base_fusion_id'],a['actor'],a['role'] in {'admin','teacher','reviewer'})
        doc=json.loads(v['payload']);doc.update(report_id=rid,version=v['version'],origin=v['origin'],created_at=v['created_at'],status=base['status'],options=json.loads(r['options']))
        if base['status']!='current':doc.update(sections=[],evidence=[],reason=base.get('reason'))
        return doc
    def generate(options,a,prior=None,draft='',expected_version=None,preserved=None):
        request={'run_id':options.run_id,'question':'请按固定报告范式生成完整复盘，结合所有所选资料；'+options.focus,'report_template':options.angle,'report_context':draft,'roster_subject_id':options.roster_subject_id,'track_id':options.track_id,'start':options.start,'end':options.end,'document_ids':options.document_ids,'material_mode':options.material_mode,'use_llm':options.use_llm,'allow_verification':False,'budget_seconds':180,'max_model_calls':2}
        result=workflow.ask(request,a['actor'],a['role'] in {'admin','teacher','reviewer'})
        if result['status']!='current':raise ValueError('report_sources_changed_during_generation')
        doc=section_document(result,options);rid=prior or uid()
        if preserved:
            keep={s['section_id']:s for s in preserved};known={e['evidence_id'] for e in doc['evidence']}
            doc['sections']=[{**keep[s['section_id']],'evidence_refs':[r for r in keep[s['section_id']]['evidence_refs'] if r in known]} if s['section_id'] in keep else s for s in doc['sections']]
            doc['teacher_edited_sections']=list(keep)
            doc['source_kind']='model_report_with_preserved_teacher_authored_sections'
        with service.repo.connection() as c:
            if prior and expected_version is not None:
                current=c.execute('SELECT MAX(version) FROM lesson_report_versions WHERE report_id=?',(prior,)).fetchone()[0]
                if current!=expected_version:raise HTTPException(409,'report_edited_during_generation; reload_current_version')
            if not prior:c.execute('INSERT INTO lesson_reports VALUES(?,?,?,?,?,?)',(rid,a['actor'],options.run_id,options.angle,json.dumps(options.model_dump()),now()))
            version=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM lesson_report_versions WHERE report_id=?',(rid,)).fetchone()[0]
            c.execute('INSERT INTO lesson_report_versions VALUES(?,?,?,?,?,?,?,?)',(rid,version,result['report_id'],'model_generated',a['actor'],now(),json.dumps(doc,ensure_ascii=False),options.focus))
        return load(rid,a)
    @app.post('/api/context/lesson-reports',status_code=201)
    def new(body:Generate,a=Depends(writer)):return generate(body,a)
    @app.get('/api/context/lesson-reports')
    def listing(run_id:str,a=Depends(account)):
        with service.repo.connection() as c:rows=c.execute('SELECT report_id,angle,created_at FROM lesson_reports WHERE run_id=? AND owner=? ORDER BY created_at DESC,rowid DESC',(run_id,a['actor'])).fetchall()
        return [dict(r) for r in rows]
    @app.get('/api/context/lesson-reports/{rid}')
    def get(rid:str,a=Depends(account)):return load(rid,a)
    @app.put('/api/context/lesson-reports/{rid}')
    def edit(rid:str,body:Edit,a=Depends(writer)):
        r,v=stored(rid,a['actor']);doc=load(rid,a)
        if doc['status']!='current':raise HTTPException(409,'report_sources_stale')
        if body.version!=v['version']:raise HTTPException(409,'report_version_changed; reload_before_editing')
        if [s.section_id for s in body.sections]!=[s['section_id'] for s in doc['sections']]:raise ValueError('report_template_sections_must_be_preserved')
        known={e['evidence_id'] for e in doc['evidence']}
        if any(not set(s.evidence_refs)<=known for s in body.sections):raise ValueError('edited_report_unknown_evidence_reference')
        before={s['section_id']:s['content'] for s in doc['sections']}
        doc['teacher_edited_sections']=list(dict.fromkeys(doc.get('teacher_edited_sections',[])+[s.section_id for s in body.sections if s.content!=before[s.section_id]]))
        doc['sections']=[s.model_dump() for s in body.sections];doc['source_kind']='teacher_edited_report_not_automatic_behavior_confirmation'
        with service.repo.connection() as c:c.execute('INSERT INTO lesson_report_versions VALUES(?,?,?,?,?,?,?,?)',(rid,v['version']+1,v['base_fusion_id'],'teacher_edited',a['actor'],now(),json.dumps(doc,ensure_ascii=False),'Teacher edited report text; original model and behavior records retained.'))
        return load(rid,a)
    @app.post('/api/context/lesson-reports/{rid}/revise',status_code=201)
    def revise(rid:str,body:Revise,a=Depends(writer)):
        r,v=stored(rid,a['actor'])
        if body.version!=v['version']:raise HTTPException(409,'report_version_changed')
        previous=json.loads(v['payload']);options=Generate.model_validate_json(r['options']).model_copy(update={'focus':body.instruction})
        draft=json.dumps(previous['sections'],ensure_ascii=False)[:16000]
        preserved=[s for s in previous['sections'] if s['section_id'] in previous.get('teacher_edited_sections',[])] if body.keep_teacher_edits else None
        return generate(options,a,rid,draft,body.version,preserved)
    @app.get('/api/context/lesson-reports/{rid}/history')
    def history(rid:str,a=Depends(account)):
        stored(rid,a['actor'])
        with service.repo.connection() as c:return [dict(r) for r in c.execute('SELECT version,origin,actor,created_at,instruction FROM lesson_report_versions WHERE report_id=? ORDER BY version',(rid,))]
    @app.get('/api/context/lesson-reports/{rid}/export/{format}')
    def export(rid:str,format:str,a=Depends(account)):
        doc=load(rid,a)
        if doc['status']!='current':raise HTTPException(409,'report_sources_stale')
        if format=='json':text=json.dumps(doc,ensure_ascii=False,indent=2);mime='application/json'
        elif format=='markdown':
            text='# '+doc['title']+'\n\n'+doc['scope_warning']+'\n\n'+'\n\n'.join('## '+s['heading']+'\n\n'+s['content']+'\n\n来源：'+', '.join(s['evidence_refs']) for s in doc['sections']);mime='text/markdown'
        else:raise ValueError('unsupported_report_format')
        return Response(text,media_type=mime,headers={'Content-Disposition':f'attachment; filename="lesson-report-{rid}.{"md" if format=="markdown" else "json"}"'})

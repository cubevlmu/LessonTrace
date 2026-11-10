"""Readable, source-grounded prose. The LLM organizes admitted sentences; it cannot add facts."""
import json
import time
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from filelock import FileLock,Timeout
from .observation_summary import observation_summary

HEADINGS={'overview':'本次复盘范围','observations':'可观察行为','materials':'结合教学与学习资料','analysis':'综合分析','recommendations':'教师建议','limits':'结论边界'}
SOURCE_NAMES={'teacher_plan':'教案','activity_schedule':'课堂活动安排','assignment':'作业要求','student_work':'学生作业','graded_work':'作业批改','assessment_result':'测评记录','rubric':'评价标准','teacher_observation':'教师观察','teacher_note':'教师说明','classroom_memory':'教师课堂记忆','learning_material':'学习资料','course_info':'课程信息','learning_goals':'教学目标','other_material':'补充资料'}

class Paragraph(BaseModel):
    model_config=ConfigDict(extra='forbid')
    section:Literal['overview','observations','materials','analysis','recommendations','limits']
    sentence_ids:list[str]=Field(min_length=1,max_length=8)

class NarrativePlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    paragraphs:list[Paragraph]=Field(min_length=1,max_length=10)


def sentences(report,plan):
    units=[];registry={e['evidence_id']:e for e in report['evidence']}
    def add(section,text,refs=(),mandatory=False):
        refs=list(dict.fromkeys(r for r in refs if r in registry))
        units.append({'sentence_id':'s'+str(len(units)),'section':section,'text':text,'evidence_refs':refs,'mandatory':mandatory})
    when=plan.effective_time_range
    display=(report.get('product_scope') or {}).get('subject_name')
    prefix=f'本次按教师选择的“{display}”资料关联范围整理' if display else f'本次针对匿名人物轨迹{plan.track_id:03d}整理' if plan.track_id else '本次针对当前选定录像整理'
    add('overview',prefix+(f'，覆盖来源时间{when[0]:g}至{when[1]:g}秒。' if when else '，当前请求没有可播放的视频覆盖。'),mandatory=True)
    if plan.output_mode=='report':add('overview','本次结果限于选定录像的分析范围，不能扩展为整节课的结论。',mandatory=True)
    if plan.roster_subject_id:
        add('overview','人物范围取自当前有效的班级轨迹登记或独立身份关联；轨迹登记名称不等于自动识别出的真实身份。',mandatory=True)
        if not plan.profile_intervals:add('limits','该人物尚无当前录像的有效关联区间，本报告只能分析选定文字资料，不能给出其个人视频行为结论。',mandatory=True)
    records=observation_summary(report['claims']+report.get('additional_claims',[]),report['evidence'],limit=30)
    report['recognition_records']=records
    for record in records[:10]:
        if not record.get('observation_available',True):
            add('limits',f'在{record["time_label"]}这次取证中，模型未能清楚辨认目标动作。这是局部取证限制，不能据此判断人物在整个时段缺席，也不代表其他人物的课堂状态。',record['evidence_refs'],True)
            continue
        count=record.get('sample_count',1);intro='多个实际取证时刻' if count>1 else '一个实际取证时刻'
        add('observations',f'在{record["time_label"]}，模型针对所选范围记录到“{record["text"]}”。这条描述有{intro}作为引用，属于AI画面观察，尚不能代替教师独立核对。',record['evidence_refs'])
    human_count=0
    for e in report['evidence']:
        p=e['provenance']
        if e['evidence_type'] in {'human_confirmed_event','event_attribution'} and e['verification_status']=='supported' and p.get('status') in {'confirmed','human_confirmed'}:
            human_count+=1;add('observations',f'教师已在所引来源时刻{e["time_scope"]}核对“{e["content"]}”。这项人工记录优先用于本次分析，适用范围限于实际审核的人物与时间。',[e['evidence_id']])
        if e['evidence_type']=='person_track' and (plan.track_id or plan.roster_subject_id or plan.intent=='track_timeline'):
            spans='、'.join(f'{a:g}至{b:g}秒' for a,b in p.get('visible_segments',[]))
            add('observations',f'所选人物轨迹的实际可见区间为{spans}。轨迹空档不补位置，未审核的连续性不能当作真实身份证明。',[e['evidence_id']])
    if not records and not human_count and plan.intent not in {'teaching_plan','cognitive_boundary','track_timeline'}:add('observations','当前筛选范围没有可直接列出的有效画面描述；这并不表示相关动作没有发生。')
    used_docs=set();material_refs=[];memories=[]
    for e in report['evidence']:
        if e['evidence_type'] not in {'teacher_plan','teacher_report'}:continue
        p=e['provenance'];did=p.get('document_id');kind=p.get('source_kind','teacher_note')
        if did in used_docs:continue
        used_docs.add(did);title=p.get('title') or SOURCE_NAMES.get(kind,'教学资料')
        text=' '.join(e['content'].split())[:1000]
        example='（项目示例）' if p.get('is_example') else ''
        add('materials',f'本次参考{SOURCE_NAMES.get(kind,"教学资料")}《{title}》{example}。其中记载：“{text}”。'+('这是教师提供的课堂背景，不是模型从录像中识别出的事实。' if kind=='classroom_memory' else '这里只引用资料内容，不据此反推课堂动作或学生学习效果。'),[e['evidence_id']],mandatory=True)
        material_refs.append(e['evidence_id'])
        if kind=='classroom_memory':memories.append(e['evidence_id'])
    visual_refs=[r for record in records for r in record['evidence_refs']]
    if records and material_refs:
        add('analysis','视频观察与所选资料提供了不同层面的信息：画面用于描述外显动作，教案、作业和教师补充用于说明任务背景。可以将动作时刻与作业要求逐项核对，但资料中出现某项任务，并不能证明画面中的人物已经完成该任务。',visual_refs[:3]+material_refs[:3],True)
        mapped=any(e['evidence_type']=='teaching_activity' and e['provenance'].get('alignment',{}).get('status')=='verified_mapping' for e in report['evidence'])
        if not mapped:add('analysis','当前所选资料尚无完整、明确的视频时间对齐依据，以上联合分析属于背景参照，不将某项活动精确安放到未经确认的视频时刻。',material_refs[:2],True)
    if report['conflicts']:add('analysis','不同来源存在需要核对的分歧。本报告保留双方记录，不能仅选择其中一种来源覆盖另一种；请结合下方引用检查具体时段与动作定义。',[r for risk in report['conflicts'] for r in risk['evidence_refs']],True)
    if plan.intent=='cognitive_boundary':add('analysis','可观察的姿态、书写或手机操作不能单独证明专注程度、心理状态、学习能力或学习效果；现有信息不能支持这种确定评价。',mandatory=True)
    kinds={e['provenance'].get('source_kind') for e in report['evidence']}
    if 'student_work' in kinds and 'rubric' in kinds:add('recommendations','建议把所选作业与评价标准逐条对照，保留具体题目、语句和对应批注；没有原始评分记录时，不自动生成分数。',material_refs[:3])
    if 'graded_work' in kinds:add('recommendations','建议将批改意见定位到原作业的具体位置，核对修改前后的内容，再形成针对性的反馈，不把单份批改记录扩大为对整体学习能力的评价。',material_refs[:3])
    if any('书写' in r['text'] for r in records):add('recommendations','对于多次记录到书写的时段，可以进一步核对笔记或作业中的实际文字是否符合任务要求；是否需要额外讲解，应结合具体作答和教师观察决定。',visual_refs[:2]+material_refs[:2])
    add('recommendations','建议先回放有代表性的观察时刻，核对人物与动作，再结合对应作业内容和评价标准补充教学反馈。对个人的具体评价应以实际作业证据和教师核对结果为依据，避免用单一动作替代学习评价。')
    add('limits','本报告只覆盖所选视频、人物区间及已引用资料。离散采样之间的动作、未关联人物和未分析时段不作推断；多次模型描述也不等同于独立人工真值。',mandatory=True)
    if any(e['provenance'].get('is_example') for e in report['evidence']):add('limits','引用的项目示例仅用于演示资料联动，不代表原授课教师的真实教案或原学生实际作业。',mandatory=True)
    return units


def compose(report,plan,provider,enabled,timeout,lock_path):
    units=sentences(report,plan);registry={u['sentence_id']:u for u in units};record={'status':'deterministic_grounded_prose','chat_attempted':False,'prompt_version':'grounded-narrative-v1'};draft=None
    analysis=None
    if enabled and timeout>1 and callable(getattr(provider,'write',None)):
        try:
            from src.inference.classroom_writer import analyze
            with FileLock(str(lock_path)).acquire(timeout=0):analysis,record=analyze(provider,report,plan,timeout)
        except Timeout:record.update(status='model_busy_grounded_fallback')
        except Exception as e:record.update(status='model_failed_grounded_fallback',error_type=type(e).__name__)
    elif enabled and timeout>1 and callable(getattr(provider,'compose_narrative',None)):
        try:
            with FileLock(str(lock_path)).acquire(timeout=0):draft,record=provider.compose_narrative(plan,units,timeout)
        except Timeout:record.update(status='model_busy_grounded_fallback')
        except Exception as e:record.update(status='model_failed_grounded_fallback',error_type=type(e).__name__)
    paragraphs=[];seen=set()
    if draft is not None:
        try:
            draft=NarrativePlan.model_validate(draft)
            for p in draft.paragraphs:
                if any(i not in registry or registry[i]['section']!=p.section for i in p.sentence_ids):raise ValueError('narrative_reference_or_section_mismatch')
            for p in draft.paragraphs:
                ids=[i for i in p.sentence_ids if i not in seen]
                if ids:paragraphs.append((p.section,ids));seen.update(ids)
        except ValueError:paragraphs=[];seen=set();record.update(status='invalid_model_outline_grounded_fallback')
    # Mandatory source/uncertainty paragraphs cannot be hidden by model selection.
    for section in HEADINGS:
        missing=[u['sentence_id'] for u in units if u['section']==section and (u['mandatory'] or not any(s==section for s,_ in paragraphs)) and u['sentence_id'] not in seen]
        if missing:paragraphs.append((section,missing));seen.update(missing)
    # Keep one stable reading order even when the model proposes a different layout.
    paragraphs.sort(key=lambda p:list(HEADINGS).index(p[0]))
    rendered=[{'heading':HEADINGS[section],'text':' '.join(registry[i]['text'] for i in ids),'evidence_refs':list(dict.fromkeys(r for i in ids for r in registry[i]['evidence_refs'])),'sentence_ids':ids} for section,ids in paragraphs]
    if analysis is not None:
        background={e['evidence_id']:e for e in report.get('_classroom_background',[])}
        refs={r for p in analysis.paragraphs for r in p.evidence_refs};known={e['evidence_id'] for e in report['evidence']}
        report['evidence'] += [background[r] for r in sorted(refs-known) if r in background]
        rendered=[p for p in rendered if p['heading'] not in {HEADINGS['analysis'],HEADINGS['recommendations']}]+[{'heading':('AI分析：' if p.section=='analysis' else '教学建议：')+p.title if p.title else 'AI综合分析' if p.section=='analysis' else '教学建议与跟进','text':p.text,'evidence_refs':p.evidence_refs,'sentence_ids':[],'content_kind':'model_analysis_not_independent_fact','analysis_section':p.section} for p in analysis.paragraphs]
        order={'本次复盘范围':0,'可观察行为':1,'结合教学与学习资料':2,'AI综合分析':3,'教学建议与跟进':4,'结论边界':5};rendered.sort(key=lambda p:3 if p.get('analysis_section')=='analysis' else 4 if p.get('analysis_section')=='recommendations' else order.get(p['heading'],4))
        # A fluent model paragraph cannot suppress source conflicts or missing temporal anchors.
        for u in units:
            if u['section']=='analysis' and u['mandatory']:rendered.insert(max(0,len(rendered)-1),{'heading':'依据与限制','text':u['text'],'evidence_refs':u['evidence_refs'],'sentence_ids':[u['sentence_id']]})
    report['narrative']=rendered;report['narrative_generation']=record
    report['audit']['narrative_sentence_catalog']=units
    report['narrative_text']='\n\n'.join(p['heading']+'\n'+p['text'] for p in rendered)
    if plan.output_mode=='report':report['answer']=rendered[0]['text'] if rendered else report['answer']

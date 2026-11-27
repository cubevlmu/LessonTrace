"""Readable, source-grounded prose. The LLM organizes admitted sentences; it cannot add facts."""
import json
import time
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from filelock import FileLock,Timeout
from .observation_summary import observation_summary
from src.inference.report_evidence import demo_notice

HEADINGS={'overview':'本次复盘范围','observations':'可观察行为','materials':'结合教学与学习资料','analysis':'综合分析','recommendations':'教师建议','limits':'结论边界'}
SOURCE_NAMES={'teacher_plan':'教案','activity_schedule':'课堂活动安排','assignment':'作业要求','student_work':'学生作业','graded_work':'作业批改','assessment_result':'测评记录','rubric':'评价标准','teacher_observation':'教师观察','teacher_note':'教师说明','classroom_memory':'教师课堂记忆','learning_material':'学习资料','course_info':'课程信息','learning_goals':'教学目标','other_material':'补充资料'}

class Paragraph(BaseModel):
    model_config=ConfigDict(extra='forbid')
    section:Literal['overview','observations','materials','analysis','recommendations','limits']
    sentence_ids:list[str]=Field(min_length=1,max_length=8)

class NarrativePlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    paragraphs:list[Paragraph]=Field(min_length=1,max_length=10)


def _fmt_sec(sec):
    total = max(0, int(round(float(sec))))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f'{h:02d}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def sentences(report,plan):
    units=[];registry={e['evidence_id']:e for e in report['evidence']}
    def add(section,text,refs=(),mandatory=False):
        refs=list(dict.fromkeys(r for r in refs if r in registry))
        units.append({'sentence_id':'s'+str(len(units)),'section':section,'text':text,'evidence_refs':refs,'mandatory':mandatory})
    when=plan.effective_time_range
    display=(report.get('product_scope') or {}).get('subject_name')
    prefix=f'对象：{display}' if display else f'对象：人物 {plan.track_id:03d}' if plan.track_id else '对象：当前课堂选定片段'
    time_str=f'（{_fmt_sec(when[0])}–{_fmt_sec(when[1])}）' if when else '（当前请求无视频覆盖）'
    roster_note='（按班级登记关联，非独立身份核验）' if plan.roster_subject_id else ''
    scope_tail='，不代表整节课结论。' if plan.output_mode=='report' else '。'
    add('overview',f'{prefix}{time_str}{roster_note}。本复盘仅依据所选片段与关联教学资料整理{scope_tail}',mandatory=True)
    if plan.roster_subject_id and not plan.profile_intervals:
        add('limits','该学生尚无当前录像的有效关联区间，以下分析仅基于其文字资料。',mandatory=True)
    records=observation_summary(report['claims']+report.get('additional_claims',[]),report['evidence'],limit=30)
    report['recognition_records']=records
    action_groups={}
    unavailable_refs=[]
    for record in records:
        if not record.get('observation_available',True):
            unavailable_refs.extend(record['evidence_refs'])
            continue
        # Equivalent literal descriptions may share a summary. The citations
        # still refer to individual observations, never a continuous action.
        action = {'用笔在纸上写字':'书写','用右手在纸上写字':'书写'}.get(record['text'],record['text'])
        action_groups.setdefault(action,[]).extend(record['evidence_refs'])
    for action, refs in sorted(action_groups.items(), key=lambda item:-len(item[1]))[:4]:
        distinct = {}
        for ref in refs:
            span = registry[ref]['time_scope']
            distinct.setdefault(tuple(span),ref)
        times = sorted(distinct)
        examples = [times[0],times[-1]] if len(times)>1 else times
        labels = '、'.join(_fmt_sec(a) if a==b else f'{_fmt_sec(a)}–{_fmt_sec(b)}' for a,b in examples)
        add('observations',f'画面观察记录为“{action}”（取证位置：{labels}）。',
            [distinct[t] for t in examples])
    if unavailable_refs:
        add('limits','部分采样时刻未能清晰辨认目标动作，不据此推断人物缺席。',unavailable_refs[:6],True)
    human_count=0
    for e in report['evidence']:
        p=e['provenance']
        if e['evidence_type'] in {'human_confirmed_event','event_attribution'} and e['verification_status']=='supported' and p.get('status') in {'confirmed','human_confirmed'}:
            human_count+=1
            ts=e.get('time_scope')
            ts_str=f'{_fmt_sec(ts[0])}–{_fmt_sec(ts[1])}' if ts and len(ts)==2 else ''
            add('observations',f'教师已核对记录（{ts_str}）：{e["content"]}。',[e['evidence_id']])
        if e['evidence_type']=='person_track' and (plan.track_id or plan.roster_subject_id or plan.intent=='track_timeline'):
            spans='、'.join(f'{_fmt_sec(a)}–{_fmt_sec(b)}' for a,b in p.get('visible_segments',[]))
            if spans:
                add('observations',f'该人物在画面中可见的时段：{spans}。',[e['evidence_id']])
    if not action_groups and not human_count and plan.intent not in {'teaching_plan','cognitive_boundary','track_timeline'}:
        add('observations','当前筛选范围暂无可直接列出的清晰画面记录。')
    doc_entries={}
    for e in report['evidence']:
        if e['evidence_type'] not in {'teacher_plan','teacher_report'}:continue
        p=e['provenance'];did=p.get('document_id');kind=p.get('source_kind','teacher_note')
        text=' '.join(e['content'].split())
        entry=doc_entries.setdefault(did,{'kind':kind,'title':p.get('title') or SOURCE_NAMES.get(kind,'教学资料'),'is_example':bool(p.get('is_example')),'best_text':'','best_ref':e['evidence_id'],'refs':[]})
        entry['refs'].append(e['evidence_id'])
        if not entry['best_text'] or (demo_notice(entry['best_text']) and not demo_notice(text)):
            entry['best_text']=text[:220] + ('…' if len(text)>220 else '')
            entry['best_ref']=e['evidence_id']
    material_refs=[]
    if doc_entries:
        mat_lines=[]
        for entry in doc_entries.values():
            tag='（示例）' if entry['is_example'] else ''
            kind_name=SOURCE_NAMES.get(entry['kind'],'教学资料')
            excerpt='' if demo_notice(entry['best_text']) else f'：{entry["best_text"]}'
            mat_lines.append(f'• {kind_name}《{entry["title"]}》{tag}{excerpt}')
            material_refs.append(entry['best_ref'])
        add('materials','本次关联的教学与学习资料：\n' + '\n'.join(mat_lines),material_refs,mandatory=True)
    visual_refs=[r for record in records for r in record['evidence_refs']]
    if records and material_refs:
        add('analysis','已结合画面外显表现与关联教学资料进行对照；具体环节对应与作业完成情况建议结合课堂实际任务进一步核查。',visual_refs[:3]+material_refs[:3],True)
    if report['conflicts']:
        add('analysis','部分来源记录存在分歧，已保留各方原文与画面引用，请点击下方来源核对。',[r for risk in report['conflicts'] for r in risk['evidence_refs']],True)
    if plan.intent=='cognitive_boundary':
        add('analysis','外显姿态或动作不能单独证明学生是否专注或听懂，需结合实际作答与课堂提问判断。',mandatory=True)
    kinds={e['provenance'].get('source_kind') for e in report['evidence']}
    recs=[]
    if 'student_work' in kinds and 'rubric' in kinds:
        recs.append('• 对照评价标准逐条核查学生作答，定位具体失分点后再给针对性反馈。')
    if 'graded_work' in kinds:
        recs.append('• 结合批改意见回到原作业对应题目，检查学生是否真正修改到位。')
    if any('书写' in k or '写字' in k for k in action_groups):
        recs.append('• 画面中出现书写时，可对照实际笔记或练习原文，核查写下的内容与任务要求是否对应。')
    if not recs:
        recs.append('• 建议回放关键观察时刻，结合学生实际作业与口头提问补充针对性反馈。')
    add('recommendations','\n'.join(recs),(visual_refs[:2]+material_refs[:2]) or material_refs[:3])
    limit_note='结论仅覆盖已分析片段与已关联资料，未分析时段不作外推。'
    if any(e['provenance'].get('is_example') for e in report['evidence']):
        limit_note+=' 标记“示例”的资料仅用于功能演示，不代表原公开课学生真实学业记录。'
    add('limits',limit_note,mandatory=True)
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
        if plan.output_mode == 'report':
            # Once the model has discussed a modality in its analysis, its
            # verbatim source catalogue belongs in citations, not a second essay.
            registry = {e['evidence_id']:e for e in report['evidence']}
            discussed = {registry[r]['evidence_type'] for p in analysis.paragraphs
                         if p.section == 'analysis' for r in p.evidence_refs}
            covered = set()
            if discussed & {'vlm_observation', 'human_confirmed_event', 'human_visual_review', 'model_discovered_event'}:
                covered.add(HEADINGS['observations'])
            if discussed & {'teacher_plan', 'teacher_report', 'teaching_activity'}:
                covered.add(HEADINGS['materials'])
            rendered = [p for p in rendered if p['heading'] not in covered]
        # Only preserve conflict alerts alongside fluent model prose; avoid injecting generic boilerplate.
        if report.get('conflicts'):
            for u in units:
                if u['section']=='analysis' and '分歧' in u['text']:
                    rendered.insert(max(0,len(rendered)-1),{'heading':'依据与限制','text':u['text'],'evidence_refs':u['evidence_refs'],'sentence_ids':[u['sentence_id']]})
    report['narrative']=rendered;report['narrative_generation']=record
    report['audit']['narrative_sentence_catalog']=units
    report['narrative_text']='\n\n'.join(p['heading']+'\n'+p['text'] for p in rendered)
    if plan.output_mode=='report':report['answer']=rendered[0]['text'] if rendered else report['answer']

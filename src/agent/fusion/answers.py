"""Three output layers: direct answer, scoped key evidence, complete audit."""
from .relevance import CONFLICTS
from .observation_summary import observation_summary


def direct(plan,claims,evidence,risks,retrieved):
    kinds={e['evidence_type'] for e in evidence};intent=plan.intent
    if plan.effective_time_range is None:return '所选真实片段不覆盖请求时间，无法判断是否发生相关行为；缺失不等于没有发生。'
    if intent=='cognitive_boundary':return '不能。外显动作、轨迹及教学计划不能证明学生专注、动机、能力或学习效果；这类判断保持未知。无需通过追加姿态帧推断心理状态。'
    if intent=='track_timeline':
        tracks=[e for e in evidence if e['evidence_type']=='person_track']
        if not tracks:answer='所选时间内没有该Track的实际观测，不能推断其在其他时刻不存在。'
        else:
            parts=[]
            for e in tracks:
                p=e['provenance'];parts.append(f"Track {p['track_id']:03d} 实际观测段为 {p['visible_segments']}（数据集全局秒）；记录中断 {len(p['gaps'])} 处。")
            answer=' '.join(parts)+' 未观测间隔不补位置；Track仅是匿名轨迹，不是已确认的学生身份，连续性尚需人工复核。'
    elif intent=='verified_person' and (not plan.subject_id or retrieved['subject_missing']):
        answer='没有当前授权且在该时间范围有效的独立身份确认，不能生成实名行为结论；匿名候选不能升级为个人事实。'
    elif plan.focus=='alignment_audit':
        answer='旧时间锚点撤销后，基于它的时间对应关系不再有效；报告须标记失效并按当前来源版本重算，保留旧版审计，不能继续当作当前事实。'
    elif plan.focus=='source_inventory':
        answer='按来源分别列出教学计划、教师记录、模型观察与知识规则。模型观察不等于人工确认；当前可访问的独立人工视觉复核数量为 '+str(sum(e['evidence_type']=='human_visual_review' for e in evidence))+'。'
    elif plan.focus=='behavior_definition':
        answer='不能只凭相似物体、姿态或同时出现的其他动作确认目标。'+' '.join(c['statement'] for c in claims if c['scope']=='versioned_policy_not_classroom_fact')
    elif intent=='conflicts':
        conflict=[r for r in risks if r['kind'] in CONFLICTS]
        answer='发现以下来源差异，双方引用均保留：'+'；'.join(dict.fromkeys(r['reason'] for r in conflict)) if conflict else '当前没有足以裁定的明确冲突。候选缺失、模型uncertain均不构成行为反证。'
    elif intent=='review_todo':
        answer='优先复核影响本问题的事项：'+'；'.join(dict.fromkeys(r['reason'] for r in risks if r['blocking_current_answer'])) if risks else '当前范围没有未处理的相关复核事项；不代表所有动作已确认。'
    elif intent=='teaching_plan':
        activities=[c for c in claims if c['category']=='activities']
        answer=' '.join(c['statement'] for c in activities) if activities else '当前范围没有可访问的关联教学计划；不能编造课堂安排。'
    else:
        observations=[e for e in evidence if e['evidence_type'] in {'dataset_annotation','vlm_observation','human_visual_review','event_attribution','model_discovered_event','human_confirmed_event'}]
        labels=sorted({e['provenance']['action'] for e in observations if 'action' in e['provenance']})
        human=[e for e in observations if e['evidence_type']=='event_attribution' and e['provenance'].get('status')=='human_confirmed']
        records=[r for r in observation_summary(claims,evidence,limit=30) if r.get('observation_available',True)][:3]
        answer=('AI画面记录：'+'；'.join(row['time_label']+'，'+row['text'] for row in records)+'。可点击对应引用回放。') if records else ('当前片段检索到以下行为候选：'+', '.join(labels)+'。类别建议请结合对应画面查看。') if labels else '当前范围尚未获得可展示的动作记录，可先查看视频及人物轨迹。'
        if labels==['unknown'] and not records:
            answer='模型尚未可靠确定13类行为，类别保持 unknown。'+' '.join(c['statement'] for c in claims if c['scope']=='model_report_at_sampled_instants_only')
        if not human:answer+=' 以上为AI观察，人物身份及具体执行者尚未人工核对。'
        if intent=='context_relation':
            mapped=any(e['evidence_type']=='teaching_activity' and e['provenance']['alignment']['status']=='verified_mapping' for e in evidence)
            answer+= ' 已有显式时间映射，只支持计划/陈述与观察的时段对应，不能证明实际参与教学任务。' if mapped else ' 没有当前有效的文本—视频时间锚点，无法确定教学活动与该时段的精确关系。'
        if intent in {'summary','report'}:answer+=f' 仅覆盖所选注册片段 {plan.effective_time_range} 秒，不能表述为整节课的结论。'
    if any(e['provenance'].get('is_example') for e in evidence):answer+=' 文本包含项目示例，不代表原授课教师的真实安排。'
    if plan.missing_time_ranges:answer+=f' 请求的 {plan.missing_time_ranges} 没有本片段覆盖，缺失不等于行为未发生。'
    if plan.clarification:answer=plan.clarification+' '+answer
    return answer


def assemble(request,plan,retrieved,canonical,adjudication,model,verification,resources,timings):
    claims=adjudication['claims'];additional=adjudication.get('additional_claims',[])
    relevant=canonical['relevant_risks'];catalog=canonical['risk_catalog']
    conflicts=[r for r in relevant if r['kind'] in CONFLICTS]
    queue=[r for r in relevant if r['kind'] not in {'untrusted_text_not_instructions','clip_not_complete_lesson'}]
    queue.sort(key=lambda r:(not r['blocking_current_answer'],r['review_id']))
    # No important blocking item is dropped to meet the preferred display count.
    primary_queue=[r for r in queue if r['blocking_current_answer']]
    primary_queue +=[r for r in queue if not r['blocking_current_answer']][:max(0,8-len(primary_queue))]
    selected_refs={r for c in claims+additional for r in c['evidence_refs']}|{r for risk in relevant for r in risk['evidence_refs']}
    evidence=[e for e in retrieved['evidence'] if e['evidence_id'] in selected_refs]
    answer=direct(plan,claims,evidence,relevant,retrieved)
    sources=[e for e in evidence if e['evidence_type'] in {'teacher_plan','teacher_report','teaching_activity'}]
    observations=[e for e in evidence if e['evidence_type'] in {'dataset_annotation','vlm_observation','human_visual_review','event_attribution','model_discovered_event','human_confirmed_event'}]
    sections=[{'heading':title,'claims':[c for c in claims if predicate(c)]} for title,predicate in [
        ('课堂与视频范围',lambda c:False),('教学计划及来源',lambda c:c['category']=='activities'),
        ('可观察行为',lambda c:c['category']=='behaviors'),('人物轨迹',lambda c:c['scope']=='observed_detection_segments_only_not_identity'),
        ('个人行为归属',lambda c:c['category']=='people' and c['scope']!='observed_detection_segments_only_not_identity'),
        ('跨模态关系',lambda c:c['temporal_alignment_status']!='not_applicable'),('证据冲突',lambda c:any(set(c['evidence_refs'])&set(r['evidence_refs']) for r in conflicts)),
        ('人工待复核事项',lambda c:False),('分析限制',lambda c:c['category']=='limits'),('完整引用索引',lambda c:False)]] if plan.output_mode=='report' else []
    return {'answer':answer,'direct_answer':{'text':answer,'evidence_refs':list(dict.fromkeys(r for c in claims for r in c['evidence_refs']))},
        'recognition_records':observation_summary(claims,evidence),
        'output_mode':plan.output_mode,'query_plan':plan.model_dump(),'claims':claims,'additional_claims':additional,'evidence':evidence,'sections':sections,
        'classroom':{'run_id':plan.run_id,'clip_id':plan.clip_id,'lesson_id':plan.lesson_id,'time_range':plan.effective_time_range,'scope':'selected_registered_clip_only_not_assumed_complete_lesson'},
        'activities':sources,'observable_behaviors':observations,'person_timeline':[e for e in evidence if e['evidence_type']=='person_track'],
        'conflicts':conflicts,'review_queue':primary_queue,'review_queue_all':catalog,
        'audit':{'canonical_facts':canonical['facts'],'retrieved_evidence':retrieved['evidence'],'critic_risks':canonical['risks'],
            'evidence_ranking':canonical['evidence_scores'],'eligible_fact_ids':canonical['eligible_fact_ids'],'mandatory_fact_ids':canonical['mandatory_fact_ids'],
            'full_relevant_review_queue':queue,'important_risk_omissions':sorted({r['review_id'] for r in queue if r['blocking_current_answer']}-{r['review_id'] for r in primary_queue})},
        'knowledge_rule_version':canonical['knowledge_version'],
        'trace':{'planning':{'method':'bounded_intent_specific_query_plan','tools_whitelisted':plan.tools},
            'miner':{'evidence_count':len(retrieved['evidence']),'query_relevant_count':len(retrieved.get('query_evidence_ids',[])),'logical_tools':plan.tools},
            'analyzer':{'canonical_fact_count':len(canonical['facts']),'relevant_fact_count':len(canonical['eligible_fact_ids']),'model_candidate':model},
            'critic':relevant,'verifier':verification,'arbiter':adjudication['candidate_checks'],'resources':resources,'timings':timings},
        'language_composition':adjudication['composition'],'limitations':['仅所选注册片段，不代表完整课堂。','计划不证明实际发生；Track不是学生身份。','程序相关性/引用检查不是独立人工事实验证。']}

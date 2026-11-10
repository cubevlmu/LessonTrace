"""Selective evaluation: abstentions remain a separate confusion-matrix column."""
from collections import Counter
import statistics


def fraction(numerator, denominator):
    return {'value':numerator/denominator if denominator else None,'numerator':numerator,'denominator':denominator,
            'status':'measured' if denominator else 'not_evaluable'}


def selective_metrics(labels, predictions):
    matrix={truth:{decision:0 for decision in ['confirmed','rejected','uncertain','error']}
            for truth in ['confirmed','rejected','uncertain']}
    for sid,truth in labels.items():
        decision=predictions.get(sid,{}).get('decision','error')
        if decision not in matrix[truth]:raise ValueError('invalid_prediction_decision')
        matrix[truth][decision]+=1
    p,n,u=matrix['confirmed'],matrix['rejected'],matrix['uncertain']
    tp,fn,fp,tn=p['confirmed'],p['rejected'],n['confirmed'],n['rejected']
    answered=tp+fn+fp+tn;definite=sum(p.values())+sum(n.values())
    recall=fraction(tp,tp+fn);specificity=fraction(tn,tn+fp)
    balanced=(recall['value']+specificity['value'])/2 if recall['value'] is not None and specificity['value'] is not None else None
    return {'confusion_matrix':matrix,'definite_reference_count':definite,'human_uncertain_count':sum(u.values()),
            'answered_definite_count':answered,'precision':fraction(tp,tp+fp),'recall':recall,
            'f1':fraction(2*tp,2*tp+fp+fn),'specificity':specificity,
            'balanced_accuracy':{'value':balanced,'status':'measured' if balanced is not None else 'not_evaluable',
                                 'formula':'0.5*(TP/(TP+FN)+TN/(TN+FP)), answered cases only',
                                 'positive_denominator':tp+fn,'negative_denominator':tn+fp},
            'answered_accuracy':fraction(tp+tn,answered),'coverage':fraction(answered,definite),
            'selective_risk':fraction(fp+fn,answered),'false_confirmations':fp,'false_rejections':fn,
            'unnecessary_abstentions_on_definite':p['uncertain']+n['uncertain'],
            'unnecessary_abstention_rate':fraction(p['uncertain']+n['uncertain'],definite),
            'correct_abstentions_on_human_uncertain':u['uncertain'],
            'correct_abstention_rate':fraction(u['uncertain'],sum(u.values())),
            'errors_on_definite':p['error']+n['error'],'errors_on_human_uncertain':u['error'],
            'human_uncertain_answered':u['confirmed']+u['rejected'],
            'coverage_all_51':fraction(answered+u['confirmed']+u['rejected'],len(labels)),
            'positive_detection_yield':fraction(tp,sum(p.values())),
            'verified_negative_yield':fraction(tn,sum(n.values())),
            'metric_rule':'Primary P/R/F1/specificity/balanced accuracy use binary answers on definite human labels only. Model uncertain and error are neither binary successes nor binary mistakes; both reduce coverage and are tabulated separately. Positive/negative yields use all corresponding human labels and are not selective recall/specificity. Unnecessary abstentions mean abstentions relative to definite A labels, not independent proof that the sampled frames were sufficient.'}


def is_wrong(decision, truth):
    return truth in {'confirmed','rejected'} and decision in {'confirmed','rejected'} and decision!=truth


def summarize_method(samples, references, runs):
    labels={s['sample_id']:references[s['sample_id']]['data']['review']['decision'] for s in samples}
    predictions={r['sample_id']:r for r in runs}
    result=selective_metrics(labels,predictions)
    result['raw_model_metrics']=selective_metrics(labels,{sid:{'decision':(r.get('raw_model_decision') or 'error') if r['decision']!='error' else 'error'} for sid,r in predictions.items()})
    per_class={}
    for action in sorted({s['target_action'] for s in samples}):
        subset={s['sample_id']:labels[s['sample_id']] for s in samples if s['target_action']==action}
        per_class[action]=selective_metrics(subset,predictions)
    result['per_class']=per_class
    overrides=Counter();filtered=Counter({'raw_correct_binary_to_uncertain':0,'raw_wrong_binary_to_uncertain':0})
    for r in runs:
        outcome=(r.get('visual_record') or {}).get('result') or {}
        overrides.update(outcome.get('override_reason') or [])
        if r['decision']=='uncertain':
            truth=labels[r['sample_id']];raw=r.get('raw_model_decision')
            if truth in {'confirmed','rejected'} and raw==truth:filtered['raw_correct_binary_to_uncertain']+=1
            if is_wrong(raw,truth):filtered['raw_wrong_binary_to_uncertain']+=1
    result['policy_effects']={'override_reason_counts':dict(overrides),**filtered,
                              'note':'Correct/wrong here means agreement/disagreement with A, not independent verification of model descriptions.'}
    if any((r.get('visual_record') or {}).get('review_rounds') for r in runs):
        first={}
        for r in runs:
            rounds=(r.get('visual_record') or {}).get('review_rounds') or []
            first[r['sample_id']]={'decision':rounds[0]['policy_outcome']['decision'] if rounds else 'error'}
        result['same_run_first_round_metrics']={**selective_metrics(labels,first),
            'scope_note':'Retrospective paired analysis of the recorded first Ours round, not a separately executed baseline. A final-stage failure may have a valid earlier first-round result. No counterfactual wall time or GPU peak is invented.',
            'actual_api_seconds_first_two_calls':sum(call['elapsed_seconds'] for r in runs for call in r.get('raw_responses',[])[:2])}
    calls=sum(r['model_calls'] for r in runs);elapsed=[r['elapsed_seconds'] for r in runs]
    memory=[r['gpu_peak_mib'] for r in runs if r.get('gpu_peak_mib') is not None]
    temperatures=[r['gpu_temperature_peak_c'] for r in runs if r.get('gpu_temperature_peak_c') is not None]
    inference=[call['elapsed_seconds'] for r in runs for call in r.get('raw_responses',[])]
    cooling=[call.get('cooldown_before_seconds',0) for r in runs for call in r.get('raw_responses',[])]
    result['cost']={'completed_attempts':len(runs),'total_vlm_calls':calls,'mean_vlm_calls':statistics.mean(r['model_calls'] for r in runs) if runs else None,
                    'total_wall_seconds':sum(elapsed),'mean_wall_seconds':statistics.mean(elapsed) if elapsed else None,
                    'total_api_seconds':sum(inference),'mean_api_seconds_per_sample':sum(inference)/len(runs) if runs else None,
                    'cooldown_seconds_inside_requests':sum(cooling),'gpu_peak_mib':max(memory) if memory else None,
                    'gpu_temperature_peak_c':max(temperatures) if temperatures else None,
                    'total_additional_calls':sum(r.get('additional_calls',0) for r in runs),
                    'total_additional_api_seconds':sum(r.get('additional_seconds',0) for r in runs),
                    'correct_binary_answers':result['confusion_matrix']['confirmed']['confirmed']+result['confusion_matrix']['rejected']['rejected']}
    active=Counter({key:0 for key in ['requested','additional_call_attempts','completed_additional_round',
                                    'wrong_binary_to_correct_binary','wrong_binary_to_abstention',
                                    'abstention_to_correct_binary','abstention_to_wrong_binary',
                                    'raw_wrong_to_correct_binary','raw_wrong_to_uncertain','raw_correct_to_wrong_binary']});transitions=Counter();details=[]
    for r in runs:
        visual=r.get('visual_record') or {};outcome=visual.get('result') or {};rounds=visual.get('review_rounds') or []
        requested=bool(outcome.get('additional_evidence_requested'))
        if requested:active['requested']+=1
        if r.get('additional_calls',0):active['additional_call_attempts']+=1
        if len(rounds)>1:
            active['completed_additional_round']+=1
            initial=rounds[0]['policy_outcome']['decision'];final=r['decision'];truth=labels[r['sample_id']]
            initial_raw=rounds[0]['model_judgment']['decision'];last_raw=rounds[-1]['model_judgment']['decision']
            transitions[initial+' -> '+final]+=1
            if is_wrong(initial,truth) and final==truth:active['wrong_binary_to_correct_binary']+=1
            if is_wrong(initial,truth) and final=='uncertain':active['wrong_binary_to_abstention']+=1
            if initial=='uncertain' and final==truth and truth!='uncertain':active['abstention_to_correct_binary']+=1
            if initial=='uncertain' and is_wrong(final,truth):active['abstention_to_wrong_binary']+=1
            if is_wrong(initial_raw,truth) and last_raw==truth:active['raw_wrong_to_correct_binary']+=1
            if is_wrong(initial_raw,truth) and last_raw=='uncertain':active['raw_wrong_to_uncertain']+=1
            if truth!='uncertain' and initial_raw==truth and is_wrong(last_raw,truth):active['raw_correct_to_wrong_binary']+=1
            before=set(rounds[0]['frame_ids']);after=set(rounds[-1]['frame_ids'])
            times={f['frame_id']:f['global_time'] for f in (visual.get('evidence') or {}).get('frames',[])}
            details.append({'sample_id':r['sample_id'],'reference':truth,'initial_policy_decision':initial,'final_decision':final,
                            'initial_raw_model_decision':initial_raw,'last_raw_model_decision':last_raw,
                            'new_global_times':[times[f] for f in rounds[-1]['frame_ids'] if f not in before and f in times],
                            'new_frame_ids':sorted(after-before)})
    result['active_evidence']={'counts':dict(active),'initial_to_final_policy_transitions':dict(transitions),'cases':details}
    result['errors']=[{'sample_id':r['sample_id'],'reference':labels[r['sample_id']],'decision':r['decision'],
                       'raw_model_decision':r.get('raw_model_decision'),'error':r.get('error'),'run_id':r['run_id']}
                      for r in runs if r['decision']=='error' or is_wrong(r['decision'],labels[r['sample_id']])]
    return result

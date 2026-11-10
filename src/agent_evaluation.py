"""Gold-only evaluation. No Excel/VLM output is ever used as human truth."""
from collections import Counter,defaultdict
import statistics
from src.human_gold import digest


def not_evaluable(reason):return {'status':'not_evaluable','value':None,'reason':reason}
def measured(value,denominator=None):return {'status':'measured','value':value,'denominator':denominator}
def ratio(n,d):return measured(n/d,d) if d else not_evaluable('zero_valid_denominator')


def gold_state(store,manifest,gold_mode='dual_annotator'):
    from src.gold_reference import single_annotator_state,reference_metadata
    if gold_mode=='single_annotator':return single_annotator_state(store,manifest)
    if gold_mode!='dual_annotator':raise ValueError('invalid_gold_mode')
    independent=[s for s in manifest['samples'] if s['split']=='independent_test']
    labels={};missing=[];details=[]
    for sample in independent:
        sid=sample['sample_id'];votes=store.independent_targets(sid);resolution=store.resolution(sid)
        valid=bool(resolution and len(votes)>=2 and len({v['annotator_id'] for v in votes})>=2
                   and set(resolution['annotation_refs'])=={v['review_id'] for v in votes})
        if valid:
            try:store.verify_source(sample)
            except (ValueError,KeyError):valid=False
        if valid:labels[sid]=resolution
        else:missing.append(sid)
        details.append({'sample_id':sid,'independent_votes':len(votes),'adjudicated':bool(resolution),'valid':valid})
    fingerprint=digest([(sid,v['resolution_id'],v['data']['review']) for sid,v in sorted(labels.items())])
    return {'eligible_samples':independent,'labels':labels,'missing':missing,'details':details,
            'gold_snapshot_sha256':fingerprint,'complete':bool(independent) and not missing,
            'metadata':reference_metadata(gold_mode,len(independent),bool(independent) and not missing)}


def classification_metrics(labels,predictions):
    """Closed, explicitly inspected target windows; abstentions count as missed positives."""
    counts=Counter();classes=defaultdict(Counter)
    for item in labels:
        decision=item['gold'];prediction=predictions[item['sample_id']]['decision'];action=item['action']
        for c in [counts,classes[action]]:
            c['total']+=1
            if decision=='uncertain':c['gold_uncertain']+=1
            else:
                c['definite']+=1
                if decision=='confirmed':
                    c['gold_positive']+=1
                    c['tp' if prediction=='confirmed' else 'fn']+=1
                elif prediction=='confirmed':c['fp']+=1
                if prediction==decision:c['correct_all_windows']+=1
                if prediction in {'uncertain','unknown'}:c['incorrect_abstentions']+=1
            if prediction in {'confirmed','rejected'}:
                c['answered']+=1
                if prediction!=decision:c['answered_errors']+=1
                if decision=='uncertain':c['answered_on_insufficient_gold']+=1
            elif prediction in {'uncertain','unknown'}:
                c['abstentions']+=1
                if decision=='uncertain':c['correct_abstentions']+=1
            else:c['execution_errors']+=1
            if prediction=='confirmed' and decision=='rejected':c['false_confirmations']+=1
            if prediction=='rejected' and decision=='confirmed':c['false_rejections']+=1
    def score(c):
        return {'counts':dict(c),'precision':ratio(c['tp'],c['tp']+c['fp']),
                'recall':ratio(c['tp'],c['tp']+c['fn']),
                'f1':ratio(2*c['tp'],2*c['tp']+c['fp']+c['fn']),
                'all_definite_window_accuracy_abstention_is_not_correct':ratio(c['correct_all_windows'],c['definite'])}
    return {'window_target_classification':score(counts),'per_class':{k:score(v) for k,v in classes.items()},
            'selective_risk':ratio(counts['answered_errors'],counts['answered']),
            'coverage':ratio(counts['answered'],counts['total']),
            'correct_abstention_rate':ratio(counts['correct_abstentions'],counts['gold_uncertain']),
            'incorrect_abstention_rate':ratio(counts['incorrect_abstentions'],counts['definite']),
            'execution_error_rate':ratio(counts['execution_errors'],counts['total']),
            'note':'Coverage includes human-uncertain cases. Error is a technical failure, not a correct refusal. Precision/recall use definite human labels only; abstentions and errors on gold positives are FN.'}


def temporal_iou(a,b):
    overlap=max(0,min(a[1],b[1])-max(a[0],b[0]));union=max(a[1],b[1])-min(a[0],b[0])
    return overlap/union if union>0 else float(a==b)


def event_metrics(labels,predictions,threshold=.5):
    definite=[item for item in labels if item['gold']!='uncertain']
    if not definite:return not_evaluable('no_definite_human_event_scope')
    if any(not item['review']['watched_full_window'] or not item['review']['events_exhaustive'] or (item['gold']=='confirmed' and not item['review']['timing_known']) for item in definite):
        return not_evaluable('human_event_boundaries_or_exhaustive_scope_incomplete')
    if any('predicted_episodes' not in predictions[item['sample_id']] for item in definite):
        return not_evaluable('current_visual_verifier_reports_occurrence_not_event_boundaries')
    counts=Counter();onset_errors=[]
    for item in definite:
        truth=item['review']['target_intervals'];pred=predictions[item['sample_id']]['predicted_episodes'];used=set()
        for episode in pred:
            best=max(((temporal_iou([episode['start'],episode['end']],[g['start'],g['end']]),i) for i,g in enumerate(truth) if i not in used),default=(0,None))
            if best[0]>=threshold and best[1] is not None:
                used.add(best[1]);counts['tp']+=1;onset_errors.append(abs(episode['start']-truth[best[1]]['start']))
            else:counts['fp']+=1
        counts['fn']+=len(truth)-len(used)
    return {'status':'measured','temporal_iou_threshold':threshold,'counts':dict(counts),
            'precision':ratio(counts['tp'],counts['tp']+counts['fp']),
            'recall':ratio(counts['tp'],counts['tp']+counts['fn']),
            'f1':ratio(2*counts['tp'],2*counts['tp']+counts['fp']+counts['fn']),
            'matched_onset_mae_seconds':measured(statistics.mean(onset_errors),len(onset_errors)) if onset_errors else not_evaluable('no_matched_boundaries')}


def claim_metrics(store,samples,runs,gold_mode='dual_annotator'):
    valid=[];errors=[];invalid_audits=[];prediction_claims={c['claim_id'] for r in runs for c in r.get('claim_items',[])}
    for sample in samples:
        resolution=store.resolution(sample['sample_id'],'claims') if gold_mode=='dual_annotator' else None
        if gold_mode=='single_annotator':
            from src.gold_schema import GoldClaimReviews
            aid=sample['campaign_id']+':annotator_a';target=store.latest(sample['sample_id'],'target',aid);ratings={}
            for record in store.reviews(sample['sample_id'],'claims'):
                if record['annotator_id']!=aid or not target or record.get('target_review_id')!=target['review_id']:continue
                try:value=GoldClaimReviews.model_validate(record['data'])
                except ValueError:
                    invalid_audits.append({'review_id':record['review_id'],'reason':'invalid_claim_rating_schema'});continue
                for rating in value.ratings:ratings[rating.claim_id]=rating.model_dump()
            if ratings:resolution={'data':{'ratings':list(ratings.values())}}
        if not resolution:continue
        actual={c['claim_id']:c for c in store.claim_items(sample)}
        for rating in resolution['data']['ratings']:
            if rating['claim_id'] not in prediction_claims or rating['claim_id'] not in actual:continue
            valid.append(rating)
            if rating.get('actual_time') is not None:
                expected=actual[rating['claim_id']]['evidence'][0]['global_time'];errors.append(abs(rating['actual_time']-expected))
    if not valid:return {**not_evaluable('no_annotator_a_claim_audits_for_this_method' if gold_mode=='single_annotator' else 'no_two_annotator_coordinated_claim_audits_for_this_method'),'invalid_audits':invalid_audits}
    definite=[r for r in valid if r['correctness']!='unable_to_judge']
    return {'status':'measured','gold_mode':gold_mode,'invalid_audits':invalid_audits,'audited_claim_count':len(valid),'predicted_claim_count':len(prediction_claims),
            'unaudited_claim_count':len(prediction_claims-{r['claim_id'] for r in valid}),'unable_to_judge':len(valid)-len(definite),
            'strict_claim_accuracy':ratio(sum(r['correctness']=='correct' for r in definite),len(definite)),
            'partial_is_not_fully_correct':sum(r['correctness']=='partially_correct' for r in valid),
            'unsupported_claim_ratio':ratio(sum(r['unsupported']=='yes' for r in valid),sum(r['unsupported']!='unknown' for r in valid)),
            'evidence_conclusion_consistency':ratio(sum(r['evidence_consistency']=='matches' for r in valid),sum(r['evidence_consistency']!='unknown' for r in valid)),
            'reference_accessibility':ratio(sum(r['reference_accessible']=='yes' for r in valid),sum(r['reference_accessible']!='unknown' for r in valid)),
            'timestamp_correctness':ratio(sum(r['timestamp_correct']=='yes' for r in valid),sum(r['timestamp_correct']!='unknown' for r in valid)),
            'human_time_location_mae_seconds':measured(statistics.mean(errors),len(errors)) if errors else not_evaluable('no_human_time_locations')}


def evaluate_campaign(store,manifest,gold_mode='dual_annotator',experiment_id=None):
    state=gold_state(store,manifest,gold_mode);samples=state['eligible_samples'];all_runs=store.experiments(manifest['campaign_id']);methods={}
    for method in ['B0','B1','B2','Ours']:
        runs={}
        for r in all_runs:
            if r['method']==method and r.get('manifest_sha256')==manifest['manifest_sha256'] and r.get('gold_snapshot_sha256')==state['gold_snapshot_sha256'] and r.get('experiment_id')==experiment_id:
                runs.setdefault(r['sample_id'],r) # first frozen-protocol attempt, never best/latest cherry-picking
        missing=[s['sample_id'] for s in samples if s['sample_id'] not in runs]
        reason=None
        if not state['complete']:reason='annotator_a_reference_incomplete_or_invalid' if gold_mode=='single_annotator' else 'independent_human_annotations_or_adjudication_incomplete'
        elif method=='B2':reason='fixed_multiframe_baseline_not_implemented_in_minimal_B1_vs_Ours_scope'
        elif missing:reason='fixed_test_cohort_predictions_incomplete'
        elif any(r.get('manifest_sha256')!=manifest['manifest_sha256'] or r.get('gold_snapshot_sha256')!=state['gold_snapshot_sha256'] for r in runs.values()):reason='manifest_or_gold_snapshot_mismatch'
        elif any(r.get('execution_kind') not in {'real_local_vlm','dataset_candidate_baseline'} for r in runs.values()):reason='non_real_predictions_not_eligible_for_official_scores'
        elif method!='B0' and any(r.get('model_id')!=manifest['experiment_protocol']['model_id'] or r.get('model_digest')!=(manifest['experiment_protocol'].get('model_snapshot') or {}).get('digest') or r.get('prompt_version')!=manifest['experiment_protocol'][method+'_prompt_version'] or r.get('definition_version')!=manifest['experiment_protocol']['definition_version'] for r in runs.values()):reason='frozen_model_prompt_or_definition_mismatch'
        if reason:
            methods[method]={'status':'not_evaluable','reason':reason,'completed_real_records':len(runs),'missing_prediction_samples':len(missing),
                             'behavior':not_evaluable(reason),'claims':not_evaluable(reason),'refusal':not_evaluable(reason),'traceability':not_evaluable(reason),'cost':not_evaluable(reason)}
            continue
        labels=[{'sample_id':s['sample_id'],'action':s['target_action'],'gold':state['labels'][s['sample_id']]['data']['review']['decision'],
                 'review':state['labels'][s['sample_id']]['data']['review']} for s in samples]
        windows_complete=all(x['review']['watched_full_window'] for x in labels if x['gold']!='uncertain')
        classification=classification_metrics(labels,runs)
        claims=claim_metrics(store,samples,list(runs.values()),gold_mode)
        costs=list(runs.values());gpu=[r['gpu_peak_mib'] for r in costs if r.get('gpu_peak_mib') is not None]
        methods[method]={'status':'evaluated','sample_count':len(samples),
                         'behavior':{'event_level':event_metrics(labels,runs),'fixed_window_target':classification['window_target_classification'] if windows_complete else not_evaluable('full_window_human_inspection_incomplete'),
                                     'per_class':classification['per_class'],'gold_uncertain_count':sum(x['gold']=='uncertain' for x in labels)},
                         'claims':claims,'refusal':{k:classification[k] for k in ['selective_risk','coverage','correct_abstention_rate','incorrect_abstention_rate','execution_error_rate']},
                         'false_confirmations':sum(runs[x['sample_id']]['decision']=='confirmed' and x['gold']=='rejected' for x in labels),
                         'false_rejections':sum(runs[x['sample_id']]['decision']=='rejected' and x['gold']=='confirmed' for x in labels),
                         'traceability':claims,
                         'cost':{'status':'measured','mean_model_calls':statistics.mean(r['model_calls'] for r in costs),'mean_elapsed_seconds':statistics.mean(r['elapsed_seconds'] for r in costs),
                                 'gpu_peak_mib':max(gpu) if gpu else None,'gpu_note':'null means unmeasured, not zero',
                                 'mean_additional_calls':statistics.mean(r.get('additional_calls',0) for r in costs),
                                 'mean_additional_evidence_seconds':statistics.mean(r.get('additional_seconds',0) for r in costs)},
                         'counts_note':classification['note']}
    return {**state['metadata'],'experiment_id':experiment_id,'campaign_id':manifest['campaign_id'],'manifest_sha256':manifest['manifest_sha256'],'gold_snapshot_sha256':state['gold_snapshot_sha256'],
            'tools_implemented':True,'human_annotation_complete':state['complete'],'valid_reference_standard':state['complete'],
            'valid_independent_gold':state['complete'] if gold_mode=='dual_annotator' else False,
            'reference_status':'ready' if state['complete'] else 'incomplete_or_invalid',
            'evaluation_status':('evaluated' if all(methods[m]['status']=='evaluated' for m in ['B1','Ours']) else 'reference_ready_predictions_missing_or_ineligible') if state['complete'] else 'reference_incomplete_or_invalid',
            'valid_reference_samples':len(state['labels']),'reference_decision_counts':state.get('reference_decision_counts',{}),
            'independent_sample_count':len(samples),'development_sample_count':len(manifest['samples'])-len(samples),
            'coordinated_independent_samples':len(state['labels']) if gold_mode=='dual_annotator' else 0,'missing_gold_samples':state['missing'],
            'progress':state['details'],'methods':methods,
            'quality_metrics_note':'No Excel label, VLM decision, development case or test double is substituted for independent human truth.',
            'generalization_note':'Unknown classroom/recording groups prevent claiming a classroom-disjoint generalization benchmark.',
            'comparison_status':'not_evaluable' if any(methods[m]['status']=='not_evaluable' for m in ['B1','Ours']) else 'evaluated'}

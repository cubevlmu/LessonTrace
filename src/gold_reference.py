"""Read-only single-annotator reference validation; never synthesize adjudication."""
from collections import Counter
from datetime import datetime

from src.gold_schema import GoldBlindReview, GoldTargetReview, GoldInspection
from src.human_gold import digest


def reference_metadata(mode, count, complete=False):
    return {'gold_mode': mode,
            'gold_source': 'annotator_a' if mode == 'single_annotator' else 'two_annotators_with_adjudication',
            'annotator_count': 1 if mode == 'single_annotator' else 2,
            'adjudicated': False if mode == 'single_annotator' else complete,
            'reference_sample_count': count}


def single_annotator_state(store, manifest):
    samples = [s for s in manifest['samples'] if s['split'] == 'independent_test']
    aid = manifest['campaign_id'] + ':annotator_a'
    labels, missing, details, fingerprints = {}, [], [], []
    for sample in samples:
        sid = sample['sample_id']
        reviews = [r for r in store.reviews(sid) if r['annotator_id'] == aid]
        targets = [r for r in reviews if r['stage'] == 'target']
        problems, warnings = [], []
        target = targets[0] if len(targets) == 1 else None
        if len(targets) != 1:
            problems.append('missing_locked_target' if not targets else 'multiple_target_records')
        if target:
            try:
                if target['sample_id'] != sid or target.get('source_kind') != 'independent_gold_annotation':
                    raise ValueError('invalid_target_provenance')
                value = GoldTargetReview.model_validate(target['data']).model_dump()
                blinds = [r for r in reviews if r['stage'] == 'blind' and r['review_id'] == target.get('blind_review_id')]
                if len(blinds) != 1:raise ValueError('missing_or_ambiguous_linked_blind_observation')
                blind = blinds[0]
                if blind.get('sample_id') != sid or blind.get('source_kind') != 'independent_gold_annotation':
                    raise ValueError('invalid_blind_provenance')
                observed = GoldBlindReview.model_validate(blind['data'])
                if datetime.fromisoformat(blind['created_at']) > datetime.fromisoformat(target['created_at']):
                    raise ValueError('blind_observation_after_target_submission')
                for observation in observed.observations:store._within(sample, observation.evidence_time)
                for interval in value['target_intervals']:
                    store._within(sample, interval['start']);store._within(sample, interval['end'])
                inspection = None
                if target.get('inspection_review_id'):
                    linked = [r for r in reviews if r['stage'] == 'inspection' and r['review_id'] == target['inspection_review_id']]
                    if len(linked) != 1:raise ValueError('missing_linked_inspection')
                    inspection = linked[0]
                    if inspection.get('sample_id') != sid or inspection.get('source_kind') != 'independent_gold_annotation':
                        raise ValueError('invalid_inspection_provenance')
                    GoldInspection.model_validate(inspection['data'])
                    if inspection.get('blind_review_id') != blind['review_id']:raise ValueError('inspection_blind_mismatch')
                    if not datetime.fromisoformat(blind['created_at']) <= datetime.fromisoformat(inspection['created_at']) <= datetime.fromisoformat(target['created_at']):
                        raise ValueError('invalid_inspection_time_order')
                if value['watched_full_window'] and not (observed.watched_full_window or inspection):
                    raise ValueError('full_inspection_not_attested')
                if value['decision'] == 'rejected' and observed.occlusion != 'none':
                    raise ValueError('occluded_region_cannot_be_certified_negative')
                store.verify_source(sample)
                if not value['watched_full_window']:warnings.append('partial_window_inspection_limits_window_metrics')
                if value['decision'] == 'uncertain':warnings.append('valid_uncertain_reference_not_a_missing_answer')
                labels[sid] = {'reference_id': target['review_id'], 'annotation_refs': [target['review_id']],
                               'source_kind': 'single_annotator_reference', 'adjudicated': False,
                               'data': {'review': value}}
                fingerprints.append((sid, target, blind, inspection))
            except (ValueError, KeyError, TypeError, OSError) as error:
                problems.append(str(error))
        if problems:missing.append(sid)
        details.append({'sample_id': sid, 'target_record_count': len(targets),
                        'reference_id': target['review_id'] if target else None,
                        'valid': not problems, 'errors': problems, 'warnings': warnings,
                        'adjudicated': False})
    complete = bool(samples) and not missing
    metadata = reference_metadata('single_annotator', len(samples))
    return {'eligible_samples': samples, 'labels': labels, 'missing': missing, 'details': details,
            'gold_snapshot_sha256': digest({'metadata': metadata, 'records': sorted(fingerprints)}),
            'complete': complete, 'metadata': metadata,
            'reference_decision_counts': dict(Counter(v['data']['review']['decision'] for v in labels.values()))}

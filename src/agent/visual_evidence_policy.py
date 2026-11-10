"""Conservative evidence policy, not a replacement for visual model inference."""
import re

DYNAMIC_ACTIONS = frozenset({'walk', 'run', 'chase', 'fight', 'handup'})
COGNITIVE = re.compile(r'\b(?:attention|attentive|inattentive|engagement|emotion\w*|cogniti\w*|learning outcomes?|active study|asleep|sleeping|unconscious(?:ness)?)\b|专注|走神|情绪|认知|学习效果|睡着|正在睡觉|已经入睡', re.I)
NEGATED_BEFORE = re.compile(r"(?:cannot|can't|could not|unable to|do not|does not|must not|should not|no evidence|insufficient evidence|not enough evidence|not establish|not infer|unknown|uncertain|无法|不能|不可|不应|没有证据|不足以|不等于|不代表|不意味着|不(?:据此|由此|因此|用来|用于)?(?:推断|判断|认定))[^.;。；!?！？]{0,100}$", re.I)
NEGATED_AFTER = re.compile(r'^.{0,35}(?:cannot be|is not|are not|remains? unknown|is unknown|is uncertain|无法|不能|未被|不确定)', re.I)


def semantic_findings(text, field='reason'):
    """Small context-aware hard guard. Findings are conservative, not semantic proof.

    Negative limitations and explicitly discussed labels are allowed. Affirmative
    claims in quotes are still checked. Adversative clauses reset negation scope.
    """
    findings=[]
    clauses=re.split(r'[.;。；!?！？]|\bbut\b|\bhowever\b|但是|但',text,flags=re.I)
    for clause in clauses:
        # A quoted *label mention* is data, unlike: the student is "attentive".
        check=re.sub(r'''\b(?:label|word|term)\s+["'“][^"'”]+["'”]''','label mention',clause,flags=re.I)
        check=re.sub(r'''(?:标签|词语)[“"'][^”"']+[”"']''','标签引用',check)
        for match in COGNITIVE.finditer(check):
            before=check[:match.start()]
            after=check[match.end():]
            # A comma introducing a new affirmative subject ends preceding negation.
            before=re.split(r'[,，]\s*(?=(?:all |the |they |students? |people |everyone |所有|学生|他们))',before,flags=re.I)[-1]
            if NEGATED_BEFORE.search(before) or NEGATED_AFTER.search(after):continue
            findings.append({'type':'semantic_guardrail_violation','field':field,
                             'term':match.group(),'clause':clause.strip(),
                             'rule':'unsupported_cognitive_assertion_or_unresolved_context'})
    return findings


def temporal_coverage(evidence, sent_frames):
    times=sorted({f['global_time'] for f in sent_frames})
    gaps=[b-a for a,b in zip(times,times[1:])]
    return {'requested_interval':evidence['requested_window'],
            'playable_interval':evidence['playable_window'],
            'sampled_global_times':times,'sample_count':len(times),
            'sample_span_seconds':times[-1]-times[0] if times else 0,
            'max_gap_seconds':max(gaps,default=None),'mode':'discrete_frames',
            'exhaustive_interval_observation':False,
            'subject_identity_verified':False,
            'partial_clip_coverage':evidence['partial_coverage'],
            'note':'Span between samples is not continuous observation; no time-coverage percentage is inferred.'}


def assess_rejection(judgment, target_claim, evidence, known_refs):
    facts=judgment.counter_evidence
    concrete=[f for f in facts if f.kind=='direct_incompatible_fact' and f.fact.strip()
              and set(f.evidence_refs)<=known_refs]
    matching=[f for f in concrete if f.target_claim==target_claim]
    region=[f for f in matching if f.subject_scope=='entire_region']
    checks={'specific_counterevidence_provided':bool(concrete),
            'counterevidence_targets_claim':bool(matching),
            'counterevidence_covers_subject_scope':bool(region),
            'whole_interval_exhaustively_observed':False,
            'independently_verified_counterfact':False}
    # For these imported existential interval claims, even true mutually-exclusive
    # postures at sample instants cannot exclude an action elsewhere in the interval.
    # Model self-reports of entire_interval do not turn sparse frames into coverage.
    reasons=[name for name,passed in checks.items() if not passed]
    return {'raw_decision':judgment.decision,'claim':target_claim,
            'claim_scope':'action_exists_somewhere_in_region_during_interval',
            'checks':checks,'sufficient_to_reject':False,'missing_conditions':reasons,
            'counter_evidence':[f.model_dump() for f in facts],
            'explanation':'Discrete samples cannot refute an existential interval claim; neither identity nor exhaustive regional coverage is independently established.'}


def decide(judgment, target_claim, evidence, frames, violations, alignment_context=None):
    """Return an explicit system override; never manufacture model observations."""
    reasons=[];assessment=None;source='visual_model';decision=judgment.decision
    if judgment.decision=='rejected':
        assessment=assess_rejection(judgment,target_claim,evidence,{f['frame_id'] for f in frames})
        decision='uncertain';source='system_evidence_policy'
        reasons.append('insufficient_counterevidence_for_interval_claim')
    if target_claim in DYNAMIC_ACTIONS and judgment.decision=='confirmed':
        refs={f['frame_id'] for f in frames if f['frame_id'] in judgment.evidence_refs}
        times=sorted(f['global_time'] for f in frames if f['frame_id'] in refs)
        if judgment.support_type!='temporal_change' or len(times)<2 or max(b-a for a,b in zip(times,times[1:]))>.55:
            decision='uncertain';source='system_evidence_policy'
            reasons.append('dynamic_action_needs_ordered_close_temporal_support')
    if target_claim in {'sleep','attention','engagement','learning_effect'} and judgment.decision=='confirmed':
        decision='uncertain';source='system_evidence_policy'
        reasons.append('sampled_posture_cannot_establish_unobservable_state')
    if violations:
        decision='uncertain';source='system_semantic_guardrail'
        reasons.append('semantic_guardrail_violation')
    alignment={}
    if judgment.decision=='confirmed':
        from src.agent.claim_evidence_alignment import check_alignment
        alignment_context=alignment_context or {}
        alignment=check_alignment(judgment,target_claim,evidence,frames,
                                  observations=alignment_context.get('observations'),context=alignment_context)
        if not alignment['passed']:
            decision='uncertain';source='system_claim_evidence_alignment'
            reasons.append('claim_evidence_mismatch')
    return {'decision':decision,'final_decision':decision,'raw_model_decision':judgment.decision,
            'decision_source':source,'safety_override':bool(reasons),
            'override_reason':reasons,'assessment':assessment,
            'support_facts':[fact.model_dump() for fact in judgment.support_facts],
            'alignment_checks':alignment}

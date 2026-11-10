from src.agent.claim_evidence_alignment import ACTION_REQUIREMENTS,VERSION as CESA_VERSION
import hashlib
import json

VERSION='context-kb-v1'
RELATIONS={'group_discussion':['chat','discuss'],'independent_practice':['homework'],'phone_quiz':['playphone']}


def rules():
    result=[]
    for action,d in ACTION_REQUIREMENTS.items():
        result.append({'rule_id':'behavior:'+action,'category':'behavior_definition','version':VERSION,'action':action,
            'observable_features':d['all'],'required_evidence':d,'ambiguities':d['description'],
            'cannot_infer':['identity','attention','learning_outcome','misconduct'],'source':'src/agent/claim_evidence_alignment.py:ACTION_REQUIREMENTS','source_version':CESA_VERSION,
            'note':'Existing class definitions retained; these are operational evidence requirements, not calibrated recognition guarantees.'})
    for activity,actions in RELATIONS.items():
        result.append({'rule_id':'activity:'+activity,'category':'activity_context','version':VERSION,'activity':activity,'possible_actions':actions,
            'rule':'May supply task background; neither proves observed action nor validates an individual executor.','source':'11D project evidence policy'})
    for key,text in {'independent_sources':'Cite video observations and teacher text separately.','plan_not_fact':'A planned activity does not prove it occurred.',
            'no_upgrade':'Model or textual assertion never becomes human confirmation.','no_identity_from_text':'Names in documents cannot establish an identity binding.',
            'no_group_to_person':'Group activity cannot establish a specific executor.','no_mental_state':'Observable actions cannot establish cognition or learning outcomes.',
            'alignment_required':'Precise temporal relations require current confirmed source-scoped mapping.'}.items():
        result.append({'rule_id':'evidence:'+key,'category':'evidence_policy','version':VERSION,'rule':text,'source':'11D project evidence policy'})
    return result


def digest():return hashlib.sha256(json.dumps(rules(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()

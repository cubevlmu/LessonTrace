"""Blind physical description then classification; no Excel/event/filename access."""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
import httpx
from pydantic import ValidationError
from src.attribution.visual import ActorReviewer,subject_gate
from src.attribution.service import overlap
from src.attribution.transport import SubjectVLM
from src.attribution.schemas import ActorObservations
from src.agent.visual_reviewer import VLMConfig
from src.agent.visual_evidence_policy import decide,semantic_findings
from src.agent.claim_evidence_alignment import ACTION_REQUIREMENTS
from src.agent.fusion.evidence import version
from src.identity.store import uid,now
from .schemas import Classification,PROMPT_VERSION,CLASSIFICATION_PROMPT_VERSION,VERSION


# A single still can support contact/location but not a change of position.
# Match only complete, affirmative clauses. Preserve negation and other actors.
_HAND_LOCATION = r'(?:左手|右手|双手|手)(?:放)?在(?:桌面|纸面|纸张|书本|课本|桌子|纸|桌|书)上'
_SINGLE_MOTION = re.compile(rf'({_HAND_LOCATION})(?:来回)?(?:移动|滑动|划动)')
_ALTERNATIVES = re.compile(rf'({_HAND_LOCATION})[，,]正在[^，,。；;或]{{1,20}}或[^，,。；;或]{{1,20}}')


def ground_observations(answer, frames):
    """Retain explicit visible locations when a still cannot support the claimed motion."""
    times = {f['frame_id']: f['local_time'] for f in frames}
    actions, changes = [], []
    seen = set()
    for observation in answer.observed_actions:
        text = observation.action
        if len({times[ref] for ref in observation.evidence_refs}) == 1:
            clauses = re.split('[，,]', text.rstrip('。'))
            matches = [_SINGLE_MOTION.fullmatch(clause) for clause in clauses]
            alternatives = _ALTERNATIVES.fullmatch(text.rstrip('。'))
            if all(matches):
                text = '，'.join(m[1] for m in matches)
            elif alternatives:
                text = alternatives[1]
        if text != observation.action:
            changes.append({'raw_action': observation.action, 'display_action': text,
                            'evidence_refs': observation.evidence_refs, 'reason': 'single_frame_location_only'})
        identity = (text, frozenset(observation.evidence_refs))
        if identity in seen:
            changes.append({'raw_action': observation.action, 'display_action': text,
                            'evidence_refs': observation.evidence_refs, 'reason': 'duplicate_action_and_references'})
            continue
        seen.add(identity)
        actions.append(observation.model_copy(update={'action': text}))
    return answer.model_copy(update={'observed_actions': actions}), changes


CROP_SEQUENCE_VERSION = 'blind-track-crop-sequence-v1'


CONTEXT_SEQUENCE_VERSION = 'blind-track-local-context-v2'


def include_desk_context(frames):
    # A nearby person's hands can enter the expanded crop. Keep tight crops when
    # the source subject is small or a competing detection covers a substantial part.
    if len(frames) < 3:
        return False
    for frame in frames:
        box = frame['roi_xyxy']
        height = box[3] - box[1]
        area = (box[2] - box[0]) * height
        if height < 128 or any(overlap(box, other) >= area * .25 for other in frame['other_person_boxes']):
            return False
    return True


def crop_sequence(frames, edge, show_context):
    """Keep each source crop paired with its frame number in a single chronological image."""
    import cv2
    import numpy as np

    columns = min(len(frames), 2)
    cell = edge // 2
    rows = (len(frames) + columns - 1) // columns
    panel = np.full((rows * cell, columns * cell, 3), 28, dtype=np.uint8)
    content = []
    for index, frame in enumerate(frames):
        source_file = frame['files']['full' if show_context else 'roi']
        image = cv2.imdecode(np.frombuffer(Path(source_file).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('unreadable_source_frame')
        if show_context:
            x0, y0, x1, y1 = map(int, frame['roi_xyxy'])
            dx, dy = round((x1 - x0) * .15), round((y1 - y0) * .15)
            left, top = max(0, x0 - dx), max(0, y0 - dy // 2)
            right, bottom = min(image.shape[1], x1 + dx), min(image.shape[0], y1 + dy)
            image = image[top:bottom, left:right].copy()
            cv2.rectangle(image, (x0 - left, y0 - top), (x1 - left - 1, y1 - top - 1), (255, 220, 0), 1)
        h, w = image.shape[:2]
        scale = min((cell - 4) / w, (cell - 22) / h)
        size = max(1, round(w * scale)), max(1, round(h * scale))
        crop = cv2.resize(image, size, interpolation=cv2.INTER_LINEAR if scale >= 1 else cv2.INTER_AREA)
        x = (index % columns) * cell + (cell - size[0]) // 2
        y = (index // columns) * cell + 20 + (cell - 22 - size[1]) // 2
        panel[y:y + size[1], x:x + size[0]] = crop
        cv2.rectangle(panel, (x - 1, y - 1), (x + size[0], y + size[1]), (255, 220, 0), 1)
        cv2.putText(panel, str(index + 1), (index % columns * cell + 5, index // columns * cell + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1, cv2.LINE_AA)
        content.append({'type': 'text', 'text': f"frame_id={frame['frame_id']} panel={index + 1}"})
    ok, encoded = cv2.imencode('.jpg', panel)
    if not ok:
        raise ValueError('crop_sequence_encoding_failed')
    panel_path = Path(frames[0]['files']['roi']).with_name('crop_sequence.jpg')
    panel_path.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    for frame in frames:
        frame['files']['crop_sequence'] = str(panel_path)
        frame['sha256']['crop_sequence'] = digest
    content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode()}})
    return content


def model_info():
    from src.inference.runtime import ModelRegistry
    return ModelRegistry.from_env().info()


class Observer:
    def __init__(self,source,repo,job_id,root,config,model,deadline,transport=None):
        self.source,self.repo,self.jid,self.root,self.config,self.model,self.deadline=source,repo,job_id,Path(root),config,model,deadline
        from src.inference.runtime import ModelRegistry
        cfg=ModelRegistry.from_env().vlm_config(timeout=config.timeout,max_tokens=int(os.getenv('INTICLASS_VLM_MAX_TOKENS','1600')))
        self.vlm=SubjectVLM(cfg,transport=transport)
        self.extractor=ActorReviewer(SimpleNamespace(source=source),root,self.vlm,temperature_checks=False)
        self.cache_enabled=True
        if config.processing_profile=='efficient' and hasattr(source,'enable_frame_cache'):source.enable_frame_cache()
    def invoke(self,content,prompt,schema,frames,prompt_version=None):
        import time
        prompt_version=prompt_version or PROMPT_VERSION
        if time.monotonic()>=self.deadline:raise TimeoutError('job_time_budget_exhausted')
        key=version({'model':self.model,'prompt_version':prompt_version,'schema':schema.model_json_schema(),'prompt':prompt,
            'frames':[(f['frame_id'],f['sha256']['input_combined'],f['roi_xyxy'],f['global_time']) for f in frames]})
        if prompt_version in {CROP_SEQUENCE_VERSION, CONTEXT_SEQUENCE_VERSION}:
            key = version([key, frames[0]['sha256']['crop_sequence']])
        with self.repo.connection() as c:
            cached=c.execute('SELECT payload FROM discovery_response_cache WHERE cache_key=?',(key,)).fetchone() if self.cache_enabled else None
        if cached:return schema.model_validate_json(cached[0]),{'cache_hit':True,'cache_key':key}
        with self.repo.connection() as c:
            c.execute('BEGIN IMMEDIATE');j=c.execute('SELECT status,attempted_calls,call_limit FROM analysis_jobs WHERE job_id=?',(self.jid,)).fetchone()
            if j['status']!='running':raise InterruptedError('job_control_requested')
            if j['attempted_calls']>=j['call_limit']:raise TimeoutError('job_call_budget_exhausted')
            c.execute('UPDATE analysis_jobs SET attempted_calls=attempted_calls+1,updated_at=? WHERE job_id=?',(now(),self.jid))
        raw=[];self.vlm._context.timeout=min(self.config.timeout,max(.1,self.deadline-time.monotonic()))
        try:
            aliases = {}
            if schema is ActorObservations and frames:
                aliases = {f'f_{i}': f['frame_id'] for i, f in enumerate(frames, 1)}
                by_id = {stable: short for short, stable in aliases.items()}
                content = list(content)
                for i, part in enumerate(content):
                    if part['type'] == 'text':
                        first, rest = part['text'].split(' ', 1)
                        content[i] = {**part, 'text': 'frame_id=' + by_id[first.removeprefix('frame_id=')] + ' ' + rest}
            answer = self.vlm.call(content, prompt, schema, raw)
            if aliases:
                # Short transport IDs save generation tokens. Persist stable evidence IDs only.
                for action in answer.observed_actions:
                    if any(ref not in aliases for ref in action.evidence_refs):
                        raise ValueError('unknown_blind_observation_reference')
                    action.evidence_refs = [aliases[ref] for ref in action.evidence_refs]
                alias_token = re.compile(r'(?<![a-zA-Z0-9_])f_[1-4](?![a-zA-Z0-9_])')
                def expand(text):
                    # Free text stays within the model's original length contract.
                    return alias_token.sub(lambda m: '第' + m[0][2:] + '帧' if m[0] in aliases else m[0], text)
                answer.limitations = [expand(text) for text in answer.limitations]
                for action in answer.observed_actions:
                    action.action = expand(action.action)
                if raw:
                    raw[-1]['frame_aliases'] = aliases
            serialized=answer.model_dump_json()
            with self.repo.connection() as c:
                c.execute('INSERT INTO discovery_call_attempts VALUES (?,?,?,?,?,?)',(uid(),self.jid,key,now(),'success',json.dumps({'raw':raw,'parsed':answer.model_dump(),'model':self.model,'prompt_version':prompt_version})))
                if self.cache_enabled:c.execute('INSERT OR IGNORE INTO discovery_response_cache VALUES (?,?)',(key,serialized))
                c.execute('UPDATE analysis_jobs SET completed_calls=completed_calls+1 WHERE job_id=?',(self.jid,))
            return answer,{'cache_hit':False,'cache_key':key}
        except Exception as e:
            with self.repo.connection() as c:
                c.execute('INSERT INTO discovery_call_attempts VALUES (?,?,?,?,?,?)',(uid(),self.jid,key,now(),'error',json.dumps({'raw':raw,'error':type(e).__name__+': '+str(e),'model':self.model,'prompt_version':prompt_version})))
                c.execute('UPDATE analysis_jobs SET failed_calls=failed_calls+1 WHERE job_id=?',(self.jid,))
            raise
    def observe(self,window):
        frames = self.extractor.extract_frames(window, window['frame_indices'], window['window_id'], 'f',
                                               input_long_edge=self.config.input_long_edge)
        for f in frames:
            # Stable, opaque IDs permit exact-evidence reuse across overlapping
            # windows. Run/track/time/ROI/image all participate; never fuzzy reuse.
            f['frame_id']='f_'+version([window['run_id'],window['track_id'],f['global_time'],f['roi_xyxy'],f['sha256']['input_combined']])[:24]
        efficient = self.config.processing_profile == 'efficient'
        prompt_version = PROMPT_VERSION
        if efficient:
            with_context = include_desk_context(frames)
            prompt_version = CONTEXT_SEQUENCE_VERSION if with_context else CROP_SEQUENCE_VERSION
            content = crop_sequence(frames, self.config.input_long_edge, with_context)
        else:
            content = [item for f in frames for item in self.extractor.content([f])]
        if sum(i['type']=='image_url' for i in content)>4:raise ValueError('four_image_request_limit')
        prompt = '观察每张图右侧裁剪中的同一名目标人物，用左侧青框核对位置。先看手在什么位置、是否接触或操作可见物体，再用简短中文描述最清楚、最具体的动作。看得清手和物体时请描述实际操作。只有姿态可辨时才只描述姿态。连续画面中相同的动作只写一条，列出实际支持它的帧编号；若动作改变，分别引用各自的帧。最多两条。区分保持某姿势和真正发生移动。不要把旁人的动作归给目标，不推断意图或学习状态。观察不到时返回空 observed_actions，并在 limitations 说明遮挡或不清楚之处。'
        if efficient:
            prompt = ('格内数字对应帧编号，按时间顺序展示同一个目标人物。只报告能从画面直接核对的最具体动作。'
                      '手和物体清楚时描述接触或操作；遮挡、模糊或手在框外时只描述清楚的姿势。'
                      '不猜测物品、意图或学习状态。区分静止姿势和真正变化。相同动作合并，引用实际支持它的帧编号。'
                      '最多两条；目标不清时返回空 observed_actions。用简短中文。')
        if prompt_version == CONTEXT_SEQUENCE_VERSION:
            prompt = ('各格青框内是同一名目标，框旁物体只提供环境线索；数字对应帧编号。查看目标的双手、视线以及紧邻的物体：'
                      '能辨认物体时，用一句简短中文说明目标怎样拿着、看向或操作这个物体；有明确接触位置就写具体。'
                      '若手部被挡或物品不清，则只写清楚的身体姿态，不猜工具或意图。旁人动作不能归给目标。'
                      '合并相同动作并引用所有实际支持的帧。移动或变化至少要有前后画面；动作改变则分开记录，最多两条。'
                      '人物及动作均不清楚时返回空 observed_actions。')
        observation,first=self.invoke(content,prompt,ActorObservations,frames,
                                      prompt_version=prompt_version)
        known={f['frame_id'] for f in frames}
        if any(not set(o.evidence_refs)<=known for o in observation.observed_actions):raise ValueError('unknown_blind_observation_reference')
        observation, adjustments = ground_observations(observation, frames)
        if efficient:
            # Product behavior descriptions use this independent pass directly.
            # Strict SCB/ICAP judgments stay available in the standard verifier;
            # a physical description is never relabelled as a verified category.
            return {'observations':observation.model_dump(),'observation_adjustments':adjustments,'classification':None,
                    'classification_status':'on_demand','processing_profile':'efficient','observation_prompt_version':prompt_version,
                    'proposals':[],'frames':frames,'cache':[first], 'sampling':window,
                    'unknown_or_empty':not observation.observed_actions,
                    'category_verification':'not_performed; raw physical observations retained'}
        prompt=('Discover candidate observable actions for cyan TARGET, using ONLY the supplied images and the blind observations. '
            'Return judgments=[] when no reliable category is observable. Allowed categories: '+','.join([*ACTION_REQUIREMENTS,'other_observable_action','unknown'])+'. '
            'Do NOT force a class. At most ONE judgment and ONE support fact. Cite at most TWO representative frame IDs, and provide spatial anchors only for those same cited frames, with coordinates rounded to three decimals. Keep reason under 100 characters and each limitation under 80 characters; write concise Simplified Chinese. Do not repeat or echo the schema. Each judgment must echo target_ref="target", specify executor_relation, and cite supplied frame IDs. '
            'Supporting facts require actor_role="target", subject_ref="target" and ORIGINAL full-frame normalized spatial_anchors per cited frame. '
            'Only another person performing an action is insufficient. Use uncertain for ambiguous actors. '
            'Temporal_change requires close consecutive evidence: standing and moving boxes alone do not prove walk; camera motion and tracker drift are possible. '
            'Sleep and learning states cannot be confirmed from posture. Requirements: '+json.dumps(ACTION_REQUIREMENTS,ensure_ascii=False)+
            '. Blind observations (not ground truth): '+observation.model_dump_json())
        if observation.observed_actions:
            try:
                classification,second=self.invoke(content,prompt,Classification,frames,prompt_version=CLASSIFICATION_PROMPT_VERSION)
            except (ValidationError,httpx.HTTPError) as error:
                # A failed category response cannot erase a valid independent
                # observation. The failed raw response remains in call_attempts.
                return {'observations':observation.model_dump(),'observation_adjustments':adjustments,'classification':None,
                        'classification_status':'error','classification_error':type(error).__name__+': '+str(error)[:1200],
                        'classification_prompt_version':CLASSIFICATION_PROMPT_VERSION,
                        'proposals':[],'frames':frames,'cache':[first,{'error':'classification_schema_validation_failed','cache_hit':False}],
                        'sampling':window,'unknown_or_empty':True}
        else:
            # Empty independent observation provides no physical action to classify.
            # Preserve its limitations; do not spend another call inventing a label.
            classification=Classification(judgments=[],limitations=['No target action was independently observed; category remains unknown.'])
            second={'skipped':True,'reason':'no_independent_target_observations','cache_hit':False}
        proposals=[];risks=self.source.continuity_risks(window['run_id'],window['track_id'],window['start'],window['end'])
        for idx,j in enumerate(classification.judgments):
            if not set(j.evidence_refs)<=known:raise ValueError('unknown_discovery_frame_reference')
            violations=[v for text in [j.reason,*j.limitations,*[o.action for o in observation.observed_actions],*[f.observed_action for f in j.support_facts]] for v in semantic_findings(text,'discovery')]
            evidence={'requested_window':[window['start'],window['end']],'playable_window':[window['start'],window['end']],'sampling_exhaustive':False}
            result=decide(j,j.target_claim,evidence,frames,violations,{'frame_size':[frames[0]['width'],frames[0]['height']],
                'input_views':['input_full','input_roi'],'observations':observation.model_dump()})
            gate=subject_gate(j,frames)
            if not gate['passed'] or risks or j.target_claim in {'unknown','other_observable_action'}:
                result.update(decision='uncertain',final_decision='uncertain',safety_override=True)
                result['override_reason']+=gate['failures']+(['trajectory_jump_or_drift_unresolved'] if risks else [])
            used=[f for f in frames if f['frame_id'] in j.evidence_refs];times=sorted(f['local_time'] for f in used)
            proposals.append({'proposal_id':version([window['window_id'],idx,j.model_dump()]),'window_id':window['window_id'],
                'run_id':window['run_id'],'track_id':window['track_id'],'segment_id':window['segment_id'],'action_category':j.target_claim,
                'local_start':min(times),'local_end':max(times),'observed_time_support':times,'raw_vlm_decision':j.decision,
                'final_decision':result['decision'],'support_facts':result['support_facts'],'evidence_refs':j.evidence_refs,'frames':used,
                'blind_observations':observation.model_dump(),
                'CESA_checks':result,'subject_checks':gate,'reason':j.reason,'model':self.model,'prompt_version':PROMPT_VERSION,'classification_prompt_version':CLASSIFICATION_PROMPT_VERSION,
                'discovery_pipeline_version':VERSION,'limitations':list(dict.fromkeys(j.limitations+classification.limitations+observation.limitations+
                    ['Only sampled instants, anonymous Track; trajectory continuity not independently reviewed.']))})
        return {'observations':observation.model_dump(),'observation_adjustments':adjustments,'classification':classification.model_dump(),'proposals':proposals,
            'classification_prompt_version':CLASSIFICATION_PROMPT_VERSION,
            'frames':frames,'cache':[first,second],'sampling':window,'unknown_or_empty':not proposals}

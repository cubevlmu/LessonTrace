"""Bounded subject prompt on the existing loopback VLM client/configuration."""
import json
import os
import time
from urllib.parse import urlsplit
import httpx
from src.agent.visual_reviewer import VisualReviewer

SYSTEM=('Review only observable actions of the cyan TARGET person. Gray boxes are other people. '
        'Return strict JSON using the requested schema, cite supplied frame IDs. NEVER repeat a sentence. Reason under 240 characters, at most ONE support fact if directly supported; otherwise an empty support_facts list. '
        'Never infer student identity, attention, cognition, learning outcomes or actual sleep from posture. '
        'Sparse absence is uncertain, not counterevidence. A tracked detection is not verified identity. '
        'Other people doing an action does not prove TARGET did it.')


class SubjectVLM(VisualReviewer):
    def call(self,content,prompt,schema,raw):
        system=SYSTEM
        if schema.__name__=='ActorObservations':
            system+=' 语言要求：action和limitations的内容必须使用简体中文，写具体可见姿态或动作，不要用英文短语。JSON字段名与帧引用保持原协议。不得猜测被遮挡动作或改变观察对象。'
        ids=[i['text'].split()[0].split('=')[1] for i in content if i['type']=='text']
        from src.inference.contracts import compact_contract,bounded_discovery_contract
        original_contract=schema.model_json_schema()
        contract=compact_contract(original_contract) if os.getenv('INTICLASS_COMPACT_CONTRACT','0')=='1' else original_contract
        if schema.__name__=='Classification':contract=bounded_discovery_contract(contract)
        def restrict(node):
            if isinstance(node,dict):
                for k,v in node.items():
                    if k=='evidence_refs' and isinstance(v,dict):v['items']={'type':'string','enum':ids}
                    else:restrict(v)
            elif isinstance(node,list):
                for n in node:restrict(n)
        restrict(contract)
        body={'model':self.config.model_id,'messages':[{'role':'system','content':system},
            {'role':'user','content':[{'type':'text','text':prompt+' Allowed frame IDs: '+','.join(ids)},*content]}],
            'temperature':0,'max_tokens':self.config.max_tokens,
            'response_format':{'type':'json_schema','json_schema':{'name':schema.__name__,'strict':True,'schema':contract}}}
        native=urlsplit(self.config.base_url).port==11434 and os.getenv('INTICLASS_ACTOR_OLLAMA_NATIVE','1')=='1'
        url=self.config.base_url.rstrip('/')+'/chat/completions'
        if native:
            context=int(os.getenv('INTICLASS_ACTOR_CONTEXT','8192'))
            if context<4096 or context>8192:raise ValueError('actor_context_must_be_between_4096_and_8192')
            user_text=prompt+' Allowed frame IDs: '+','.join(ids)+'\n'+'\n'.join(i['text'] for i in content if i['type']=='text')
            body={'model':self.config.model_id,'stream':False,'messages':[{'role':'system','content':system},
                {'role':'user','content':user_text,'images':[i['image_url']['url'].split(',',1)[1] for i in content if i['type']=='image_url']}],
                'format':contract,'options':{'num_ctx':context,'num_predict':self.config.max_tokens,'temperature':0},'keep_alive':'5m'}
            body['options']['num_thread']=int(os.getenv('INTICLASS_CPU_THREADS','4'))
            if os.getenv('INTICLASS_MODEL_DEVICE')=='cpu':body['options']['num_gpu']=0
            parsed=urlsplit(self.config.base_url);url=f'{parsed.scheme}://{parsed.netloc}/api/chat'
        entry={'input_images':sum(i['type']=='image_url' for i in content),'frame_ids':ids,
               'contract_variant':'discovery-bounded-v1' if schema.__name__=='Classification' else 'business_schema',
               'contract_bytes':len(json.dumps(contract).encode()),'original_contract_bytes':len(json.dumps(original_contract).encode()),
               'timeout_seconds':getattr(self._context,'timeout',self.config.timeout),'transport':'ollama_native' if native else 'openai_compatible',
               'context_tokens':context if native else None}
        began=time.perf_counter()
        try:
            with self.client() as client:
                if native:
                    from src.inference.single_model import prepare
                    entry['runtime_management_requests']=prepare(client,f'{parsed.scheme}://{parsed.netloc}',self.config.model_id)
                r=client.post(url,json=body)
            entry.update(http_status=r.status_code,body=r.text);r.raise_for_status()
            return schema.model_validate_json(r.json()['message']['content'] if native else r.json()['choices'][0]['message']['content'])
        except httpx.HTTPError as ex:
            entry['transport_error']=type(ex).__name__;raise
        finally:
            entry['elapsed_seconds']=time.perf_counter()-began;raw.append(entry)

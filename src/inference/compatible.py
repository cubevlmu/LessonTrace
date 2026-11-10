"""OpenAI-compatible text API. No video, image or biometric inputs."""
import httpx,time,json,hashlib
from urllib.parse import urlsplit

class CompatibleProvider:
    def __init__(self,config,transport=None):self.config=config;self.model=config['model'];self.url=config['base_url'];self.transport=transport
    def generate(self,plan,facts,timeout):return None,{'status':'evidence_checked_before_writing','model_id':self.model,'chat_attempted':False}
    def compose_narrative(self,plan,units,timeout):
        from .prose import outline
        return outline(self,plan,units,timeout,remote=True)
    def write(self,messages,schema,timeout):
        body={'model':self.model,'messages':messages,'response_format':{'type':'json_object'},'temperature':0.2,'max_tokens':self.config['max_tokens'],'stream':False}
        # DeepSeek's reasoning mode is unnecessary for short, evidence-grounded prose.
        if urlsplit(self.url).hostname=='api.deepseek.com':body['thinking']={'type':'disabled'}
        record={'model_id':self.model,'prompt_version':'lesson-context-writer-v2','chat_attempted':False,'status':'fallback','network_requests':0,'input_data_sha256':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(),'privacy_scope':'authorized_classroom_text_only_no_images_or_biometrics'}
        started=time.perf_counter()
        if not self.config.get('api_key'):return None,{**record,'error_type':'missing_api_key'}
        try:
            record.update(chat_attempted=True,network_requests=1)
            with httpx.Client(trust_env=False,follow_redirects=False,timeout=min(timeout,self.config['timeout']),transport=self.transport) as c:
                r=c.post(self.url+'/chat/completions',json=body,headers={'Authorization':'Bearer '+self.config['api_key']});r.raise_for_status()
            value=json.loads(r.json()['choices'][0]['message']['content']);record.update(status='success',usage=r.json().get('usage'),provider_response_id=r.json().get('id'));return value,record
        except Exception as e:record['error_type']=type(e).__name__;return None,record
        finally:record['elapsed_seconds']=time.perf_counter()-started

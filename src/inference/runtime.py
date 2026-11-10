import os
from dataclasses import dataclass
from urllib.parse import urlsplit
import ipaddress
import httpx

@dataclass(frozen=True)
class ModelRegistry:
    visual_url:str
    visual_model:str
    context_tokens:int=8192

    @classmethod
    def from_env(cls):
        product=os.getenv('INTICLASS_PROFILE','development')=='product'
        url=os.getenv('INTICLASS_VLM_URL','http://127.0.0.1:11434/v1').rstrip('/')
        parsed=urlsplit(url)
        try:local=ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:local=False
        if not local or parsed.scheme not in {'http','https'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path!='/v1':raise ValueError('visual_endpoint_requires_literal_loopback_v1')
        model=os.getenv('INTICLASS_VLM_MODEL','qwen3-vl:2b-instruct-q4_K_M' if product else 'qwen3-vl:4b-instruct')
        return cls(url,model,int(os.getenv('INTICLASS_ACTOR_CONTEXT','8192')))

    @property
    def root_url(self):return self.visual_url[:-3]

    def info(self,transport=None):
        with httpx.Client(trust_env=False,timeout=3,**({'transport':transport} if transport is not None else {})) as client:
            response=client.get(self.root_url+'/api/tags');response.raise_for_status()
        found=next((m for m in response.json().get('models',[]) if m['name']==self.visual_model),None)
        if not found:raise ValueError('configured_visual_model_unavailable; no_download_or_fake_fallback')
        return {k:found.get(k) for k in ['name','digest','details']}

    def vlm_config(self,**options):
        from src.agent.visual_reviewer import VLMConfig
        return VLMConfig(base_url=self.visual_url,model_id=self.visual_model,**options)

class RuntimeHealth:
    @staticmethod
    def visual():
        registry=ModelRegistry.from_env()
        try:
            model=registry.info()
            return {'status':'available','model_id':registry.visual_model,'model_digest':model.get('digest'),'message':'视觉模型已登记；实际推理仍受预算和证据检查约束。'}
        except (httpx.HTTPError,ValueError,KeyError):
            return {'status':'unavailable','model_id':registry.visual_model,'model_digest':None,'message':'配置的本地视觉模型暂不可用。请联系维护者检查服务或模型安装。'}

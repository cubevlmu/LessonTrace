"""Encrypted, administrator-managed text runtime configuration in the context store."""
import json,os,ipaddress
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,model_validator
from typing import Literal
from fastapi import Depends

class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider:Literal['ollama','openai_compatible']='ollama'
    base_url:str='http://127.0.0.1:11434'
    model:str=Field('qwen3:4b-instruct',min_length=1,max_length=120)
    api_key:str|None=Field(None,max_length=1000)
    clear_key:bool=False
    timeout:int=Field(120,ge=10,le=180)
    context_chars:int=Field(28000,ge=4000,le=120000)
    max_tokens:int=Field(2600,ge=500,le=6000)
    allow_text_transfer:bool=False
    @model_validator(mode='after')
    def endpoint(self):
        p=urlsplit(self.base_url)
        try:local=ipaddress.ip_address(p.hostname or '').is_loopback
        except ValueError:local=p.hostname=='localhost'
        if p.username or p.password or p.query or p.fragment or not p.hostname:raise ValueError('invalid_model_endpoint')
        if self.provider=='ollama' and (not local or p.hostname=='localhost' or p.scheme not in {'http','https'} or p.path not in {'','/'}):raise ValueError('ollama_requires_literal_loopback_root')
        if self.provider=='openai_compatible' and ((p.scheme!='https' and not(local and p.scheme=='http')) or p.path.rstrip('/')==''):raise ValueError('compatible_api_requires_https_base_path')
        if self.provider=='openai_compatible' and not local and not self.allow_text_transfer:raise ValueError('explicit_text_transfer_authorization_required')
        self.base_url=self.base_url.rstrip('/');return self

def migrate(service):
    with service.repo.connection() as c:c.execute('CREATE TABLE IF NOT EXISTS text_runtime_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1),config BLOB NOT NULL)')

def configured(service):
    migrate(service)
    with service.repo.connection() as c:row=c.execute('SELECT config FROM text_runtime_settings WHERE singleton=1').fetchone()
    return json.loads(service.access.unseal(row[0])) if row else None

def current(service):
    saved=configured(service)
    if saved:return saved
    local=os.getenv('INTICLASS_TEXT_PROVIDER','ollama' if os.getenv('INTICLASS_PROFILE')!='product' else 'deepseek')=='ollama'
    return {'provider':'ollama' if local else 'openai_compatible','model':os.getenv('INTICLASS_TEXT_MODEL','qwen3:4b-instruct' if local else 'deepseek-chat'),'base_url':os.getenv('INTICLASS_TEXT_URL','http://127.0.0.1:11434' if local else 'https://api.deepseek.com/v1'),'timeout':120,'context_chars':28000,'max_tokens':2600,'allow_text_transfer':False}

def public(service):
    data=current(service).copy();data['key_configured']=bool(data.pop('api_key',None));data['configuration_source']='database' if configured(service) else 'environment';return data

def mount(app,service,account):
    @app.get('/api/context/model-settings')
    def get(a=Depends(account)):return public(service)
    @app.put('/api/context/model-settings')
    def put(body:Settings,a=Depends(account)):
        if a['role']!='admin':raise PermissionError('model_configuration_requires_admin')
        old=configured(service) or {};data=body.model_dump(exclude={'clear_key'})
        if not body.api_key and not body.clear_key and old.get('base_url')==body.base_url:data['api_key']=old.get('api_key')
        with service.repo.connection() as c:
            c.execute('INSERT OR REPLACE INTO text_runtime_settings VALUES(1,?)',(service.access.seal(json.dumps(data)),))
            service.repo.audit(c,'text_runtime',a['actor'],'text_model_settings_changed',{k:v for k,v in data.items() if k!='api_key'})
        return public(service)

"""One location for backend configuration and durable runtime data."""
import json
import os
from pathlib import Path

PROJECT_ROOT=Path(__file__).resolve().parents[1]

def data_root():
    return Path(os.getenv('INTICLASS_DATA_DIR',str(PROJECT_ROOT/'lesson_trace_data'))).resolve()

def data_path(*parts):return data_root().joinpath(*parts)

def apply_runtime_environment():
    path=data_path('config','backend.json')
    if path.is_file():
        for key,value in json.loads(path.read_text(encoding='utf-8')).get('environment',{}).items():
            if key.startswith('INTICLASS_') or key=='DEEPSEEK_API_KEY':os.environ.setdefault(key,str(value))

def load_runtime():
    path=data_path('config','runtime.json')
    if not path.exists():path=PROJECT_ROOT/'data/tracking_runtime.json'
    value=json.loads(path.read_text(encoding='utf-8'))
    for key in ['registry_db','tracking_db','video_root']:
        p=Path(value[key]);value[key]=str((data_root()/p).resolve() if not p.is_absolute() else p.resolve())
    value['attribution_db']=str(data_path('attribution_private','attribution.db'))
    return value

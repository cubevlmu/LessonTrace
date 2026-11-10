"""Production ASGI factory, no fixture data or model downloads."""
import json
from pathlib import Path
from src.context.app import create_context_app

def create_app():
    root=Path(__file__).resolve().parent
    from src.runtime_paths import apply_runtime_environment,load_runtime,data_path
    apply_runtime_environment();runtime=load_runtime()
    return create_context_app(runtime,identity_dir=data_path('identity_private'))

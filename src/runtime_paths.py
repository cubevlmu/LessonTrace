"""One location for backend configuration and durable runtime data."""
import json
import os
from functools import lru_cache
from pathlib import Path, PureWindowsPath

PROJECT_ROOT=Path(__file__).resolve().parents[1]

def data_root():
    return Path(os.getenv('INTICLASS_DATA_DIR',str(PROJECT_ROOT/'lesson_trace_data'))).resolve()

def data_path(*parts):return data_root().joinpath(*parts)

def apply_runtime_environment():
    path=data_path('config','backend.json')
    if path.is_file():
        for key,value in json.loads(path.read_text(encoding='utf-8')).get('environment',{}).items():
            if key.startswith('INTICLASS_') or key=='DEEPSEEK_API_KEY':os.environ.setdefault(key,str(value))

def _recorded_path(value):
    native = Path(value)
    if native.is_absolute():
        return native
    windows = PureWindowsPath(value)
    return windows if windows.is_absolute() else native


@lru_cache(maxsize=8)
def _previous_roots(root):
    manifest = root / 'config' / 'storage_migration.json'
    if not manifest.is_file():
        return ()
    previous = _recorded_path(json.loads(manifest.read_text(encoding='utf-8'))['data_root'])
    if not previous.is_absolute():
        return ()
    project = previous.parent
    roots = [previous, project / 'data']
    if project.name.lower() == 'inticlass':
        roots.append(project.with_name('inticlass-tracking11a') / 'data')
        if isinstance(previous, PureWindowsPath) and os.name != 'nt':
            # The renamed Windows checkout can be mounted under a different Linux path.
            renamed = project.with_name('LessonTrace')
            roots.extend([renamed / previous.name, renamed / 'data'])
    return tuple(roots)


def stored_path(value):
    """Resolve a persisted path from this installation, retaining the original record."""
    path = _recorded_path(value)
    if path.is_absolute() and '..' not in path.parts:
        root = data_root()
        for previous in _previous_roots(root):
            if path.is_relative_to(previous):
                return root.joinpath(*path.relative_to(previous).parts).resolve()
    if isinstance(path, PureWindowsPath) and os.name != 'nt':
        raise ValueError('windows_path_outside_recorded_data_roots')
    return Path(path).resolve()


def load_runtime(path=None):
    if path is None:
        path=data_path('config','runtime.json')
        if not path.exists():path=PROJECT_ROOT/'data/tracking_runtime.json'
    path=Path(path)
    value=json.loads(path.read_text(encoding='utf-8'))
    for key in ['registry_db','tracking_db','video_root']:
        p = _recorded_path(value[key])
        value[key] = str(stored_path(p if p.is_absolute() else data_root() / p))
    value['attribution_db']=str(data_path('attribution_private','attribution.db'))
    return value

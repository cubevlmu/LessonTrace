"""Initialize a new deployment only. Existing runtime/database contents are retained."""
import getpass
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def initialize(*, local_linux=False):
    from src.evidence_db import EvidenceDB
    from src.tracking.repository import TrackingRepository
    from src.attribution.repository import AttributionRepository
    from src.identity.store import IdentityStore
    from src.runtime_paths import data_root
    from scripts.migrate_runtime_data import alias
    data=data_root();data.mkdir(exist_ok=True)
    (data/'outputs').mkdir(exist_ok=True)
    if not local_linux:
        alias(ROOT/'data',data)
        alias(ROOT/'outputs',data/'outputs')
    (data/'config').mkdir(exist_ok=True)
    path=data/'config/runtime.json'
    if not path.exists():
        EvidenceDB(data/'evidence_replay.db');TrackingRepository(data/'person_tracks.db')
        attr=data/'attribution_private';attr.mkdir(exist_ok=True);AttributionRepository(attr/'attribution.db')
        media=data/'managed_source';media.mkdir(exist_ok=True)
        runtime={'registry_db':str(data/'evidence_replay.db'),'tracking_db':str(data/'person_tracks.db'),'video_root':str(media)}
        path.write_text(json.dumps(runtime),encoding='utf-8')
    legacy=data/'tracking_runtime.json'
    if not local_linux and not legacy.exists():legacy.hardlink_to(path)
    IdentityStore(data/'identity_private')
    access=IdentityStore(data/'context_private/access')
    with access.connection() as c:count=c.execute('SELECT COUNT(*) FROM teacher_users').fetchone()[0]
    if not count:
        user=os.getenv('INTICLASS_ADMIN_USER','admin')
        password=os.getenv('INTICLASS_ADMIN_PASSWORD')
        if local_linux and not password and sys.stdin.isatty():
            password = getpass.getpass(f'为新管理员 {user} 设置密码（至少 10 个字符）：')
        if not password or password.startswith(('REPLACE','CHANGE','example')):raise ValueError('set_unique_admin_password_in_restricted_environment_file_before_first_start')
        access.create_user(user,password,'admin','initial_setup')
        print('Initial administrator created. No credentials printed.')
    return json.loads(path.read_text())

if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-linux', action='store_true', help='Use the canonical data directory directly.')
    initialize(local_linux=parser.parse_args().local_linux)

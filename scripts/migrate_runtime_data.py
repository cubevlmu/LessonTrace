"""Move runtime directories once, with DB backups and legacy path aliases."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def alias(path,target):
    if path.exists():
        if path.resolve()!=target.resolve():raise ValueError('legacy_alias_conflict: '+str(path))
        return
    if os.name=='nt':
        quote=lambda p:"'"+str(p).replace("'","''")+"'"
        subprocess.run(['powershell.exe','-NoProfile','-Command',
                        'New-Item -ItemType Junction -Path '+quote(path)+' -Target '+quote(target)+' | Out-Null'],check=True,capture_output=True)
    else:path.symlink_to(target,target_is_directory=True)

def move_directory(source,destination):
    if source.parent.resolve()!=ROOT or not destination.absolute().is_relative_to(ROOT/'lesson_trace_data'):
        raise ValueError('directory_move_outside_runtime_scope')
    if os.name=='nt':
        quote=lambda p:"'"+str(p).replace("'","''")+"'"
        subprocess.run(['powershell.exe','-NoProfile','-Command','Move-Item -LiteralPath '+quote(source)+' -Destination '+quote(destination)+' -ErrorAction Stop'],check=True,capture_output=True)
    else:source.rename(destination)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    old=ROOT/'data';target=ROOT/'lesson_trace_data';old_outputs=ROOT/'outputs'
    for path in [old,target,old_outputs]:
        if path.parent.resolve()!=ROOT:raise ValueError('migration_target_outside_project')
    already=old.exists() and old.resolve()==target.resolve()
    if not args.apply:
        print(json.dumps({'already_migrated':already,'destination':str(target),'apply_required':not already}));return
    if already:
        print(json.dumps({'already_migrated':True,'data_root':str(target)}));return
    if target.exists() and old.exists():raise ValueError('both_runtime_roots_exist; no_overwrite')
    import psutil
    for process in psutil.process_iter(['pid','name','cmdline']):
        command=' '.join(process.info['cmdline'] or [])
        if process.pid==os.getpid() or not (process.info['name'] or '').lower().startswith('python'):continue
        if any(name in command for name in ['start_context.py','product_worker.py','validate_public_class_full_run.py']):
            try:in_project=Path(process.cwd()).resolve()==ROOT or str(ROOT).lower() in command.lower()
            except (psutil.AccessDenied,psutil.NoSuchProcess):in_project=False
            if in_project:raise RuntimeError('stop_managed_process_before_migration: '+str(process.pid))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backups=[]
    if old.exists():
        backup=old/'product_backups'/('storage_migration_'+stamp);backup.mkdir(parents=True)
        for path in old.rglob('*.db'):
            if 'product_backups' in path.parts:continue
            relative=path.relative_to(old);destination=backup/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(path) as source,sqlite3.connect(destination) as copied:source.backup(copied)
            backups.append(str(relative))
        move_directory(old,target)
    else:
        target.mkdir(parents=True,exist_ok=True)
        for backup in (target/'product_backups').glob('storage_migration_*'):
            backups.extend(str(p.relative_to(backup)) for p in backup.rglob('*.db'))
    try:alias(old,target)
    except Exception:
        if not old.exists():target.rename(old)
        raise
    config=target/'config';config.mkdir(exist_ok=True)
    runtime=target/'tracking_runtime.json'
    if runtime.exists():
        data=json.loads(runtime.read_text(encoding='utf-8'))
        for key in ['registry_db','tracking_db','video_root']:
            data[key]=str(Path(data[key]).resolve())
        canonical=config/'runtime.json';runtime.rename(canonical)
        canonical.write_text(json.dumps(data,indent=2),encoding='utf-8');runtime.hardlink_to(canonical)
    preferences=target/'frontend_private/runtime_preferences.json'
    if preferences.exists():
        canonical=config/'local_preferences.json';preferences.rename(canonical);preferences.hardlink_to(canonical)
    backend=config/'backend.json'
    if not backend.exists():backend.write_text(json.dumps({'environment':{'INTICLASS_VLM_MODEL':'qwen3-vl:2b-instruct-q4_K_M','INTICLASS_ACTOR_CONTEXT':8192,'INTICLASS_CPU_THREADS':4}},indent=2),encoding='utf-8')
    outputs=target/'outputs'
    if old_outputs.exists() and old_outputs.resolve()!=outputs.resolve():
        if outputs.exists():raise ValueError('both_output_roots_exist; no_overwrite')
        move_directory(old_outputs,outputs)
    outputs.mkdir(exist_ok=True);alias(old_outputs,outputs)
    manifest={'at':datetime.now(timezone.utc).isoformat(),'data_root':str(target),'database_backups':backups,'legacy_aliases':{'data':str(target),'outputs':str(outputs)},'files_deleted':0}
    (config/'storage_migration.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'migrated':True,'data_root':str(target),'database_backups':len(backups)}))

if __name__=='__main__':main()

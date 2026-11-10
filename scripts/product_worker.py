"""Single durable SQLite queue worker, separate from the HTTP service."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from filelock import FileLock,Timeout

def main():
    from src.runtime_paths import load_runtime,apply_runtime_environment
    apply_runtime_environment()
    p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');args=p.parse_args()
    from src.context.app import create_context_app
    from src.behavior_discovery.pipeline import execute
    from src.behavior_discovery.repository import Repository
    runtime=load_runtime()
    app=create_context_app(runtime);service=app.state.context;repo=Repository(service.repo)
    (ROOT/'data/analysis_private').mkdir(parents=True,exist_ok=True)
    lock=FileLock(str(ROOT/'data/analysis_private/queue_worker.lock'));lock.acquire(timeout=0)
    try:
        # Recover only namespaces without a live executor; completed output is retained.
        with service.repo.connection() as c:rows=c.execute("SELECT job_id,status FROM analysis_jobs WHERE status IN ('running','pausing','cancelling') AND json_extract(payload,'$.product_queue')=1").fetchall()
        for row in rows:
            joblock=FileLock(str(ROOT/'data/analysis_private'/(row['job_id']+'.lock')))
            try:joblock.acquire(timeout=0)
            except Timeout:continue
            try:
                repo.update(row['job_id'],status={'running':'paused','pausing':'paused','cancelling':'cancelled'}[row['status']])
                with service.repo.connection() as c:c.execute("UPDATE discovery_windows SET status='pending' WHERE job_id=? AND status='running'",(row['job_id'],))
                repo.audit(row['job_id'],'worker_recovery',{'prior_status':row['status']})
            finally:joblock.release()
        while True:
            import psutil
            with service.repo.connection() as c:row=c.execute("SELECT job_id FROM analysis_jobs WHERE status='queued' AND json_extract(payload,'$.product_queue')=1 ORDER BY created_at,job_id LIMIT 1").fetchone()
            if row:
                if psutil.virtual_memory().available/1048576>=int(os.getenv('INTICLASS_MIN_AVAILABLE_MIB','1536')):
                    from src.inference.runtime import ModelRegistry
                    try:ModelRegistry.from_env().info()
                    except Exception:
                        if args.once:print('queued_waiting_for_model')
                        else:time.sleep(5)
                    else:execute(service,row[0])
                elif args.once:print('queued_waiting_for_memory')
            if args.once:return
            time.sleep(2)
    finally:lock.release()

if __name__=='__main__':main()

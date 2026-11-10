"""11A tracking subprocess in the existing GPU environment, no new detector."""
import argparse
import json
import sqlite3
import sys
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
if os.getenv('INTICLASS_EXTRA_SITE'):sys.path.append(os.environ['INTICLASS_EXTRA_SITE'])
from src.tracking.pipeline import run_tracking


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--job-id',required=True);a=parser.parse_args()
    db=ROOT/'data/context_private/context.db'
    def job():
        with sqlite3.connect(db) as c:
            c.row_factory=sqlite3.Row;return dict(c.execute('SELECT * FROM analysis_jobs WHERE job_id=?',(a.job_id,)).fetchone())
    initial=job();data=json.loads(initial['payload']);conf=data['config'];runtime=json.loads((ROOT/'data/tracking_runtime.json').read_text())
    options={k:conf[k] for k in ['imgsz','confidence','stride','device','max_people','processing_roi']}
    weights=Path(os.getenv('INTICLASS_PERSON_WEIGHTS',str(ROOT/'data/weights/pretrained/yolo11s.pt')))
    result=run_tracking(runtime['registry_db'],runtime['tracking_db'],runtime['video_root'],weights,options,
        clip_id=initial['video_id'],control=lambda:job()['status'],execution_scope=a.job_id)
    print(json.dumps({'run_id':result['run_id'],'status':result['status']}),flush=True)

import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.context.app import create_context_app
from src.behavior_discovery.pipeline import execute

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job-id',required=True);a=p.parse_args()
    runtime=json.loads((ROOT/'data/tracking_runtime.json').read_text());runtime['attribution_db']=str(ROOT/'data/attribution_private/attribution.db')
    app=create_context_app(runtime);execute(app.state.context,a.job_id)

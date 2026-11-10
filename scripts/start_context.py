import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.identity.store import IdentityStore,secure_directory
from src.runtime_paths import data_path,load_runtime,apply_runtime_environment


if __name__=='__main__':
    apply_runtime_environment()
    p=argparse.ArgumentParser(description='Local teacher context and source-bound review')
    p.add_argument('--runtime',default=str(data_path('config','runtime.json')));p.add_argument('--attribution-db',default=str(data_path('attribution_private','attribution.db')))
    p.add_argument('--private-dir',default=str(data_path('context_private')));p.add_argument('--identity-private-dir',default=str(data_path('identity_private')))
    p.add_argument('--port',type=int,default=8787);p.add_argument('--create-account');p.add_argument('--role',choices=['admin','reviewer','viewer'],default='admin');p.add_argument('--create-user');p.add_argument('--password');p.add_argument('--user-role',choices=['admin','teacher','reviewer'],default='teacher')
    a=p.parse_args();secure_directory(a.private_dir)
    if a.create_user:
        import getpass
        password=a.password or getpass.getpass('New account password (minimum 10 characters): ')
        access=IdentityStore(Path(a.private_dir)/'access');user=access.create_user(a.create_user,password,a.user_role,'bootstrap');print(json.dumps(user,ensure_ascii=False))
    elif a.create_account:
        access=IdentityStore(Path(a.private_dir)/'access');token=access.bootstrap(a.create_account,a.role)
        path=access.directory/('access_'+a.role+'_'+__import__('uuid').uuid4().hex[:8]+'.txt');path.write_text(token)
        print('Read local token from: '+str(path))
    else:
        from src.context.app import create_context_app
        import uvicorn
        runtime=json.loads(Path(a.runtime).read_text());runtime['attribution_db']=a.attribution_db
        uvicorn.run(create_context_app(runtime,a.private_dir,a.identity_private_dir),host='127.0.0.1',port=a.port,access_log=False)

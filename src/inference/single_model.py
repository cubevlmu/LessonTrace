"""Explicit resident-model control; caller must hold the shared inference lock."""
import os

def prepare(client,root,model):
    if os.getenv('INTICLASS_PROFILE')!='product':return 0
    response=client.get(root+'/api/ps',timeout=5);response.raise_for_status();requests=1
    for loaded in response.json().get('models',[]):
        if loaded.get('name')!=model:
            unloaded=client.post(root+'/api/generate',json={'model':loaded['name'],'keep_alive':0},timeout=30)
            unloaded.raise_for_status();requests+=1
    return requests

"""Run inside the built image: imports, actual HTTP auth, static app, codec availability."""
import json,os,subprocess,time,urllib.request,urllib.parse,http.cookiejar
import cv2,av,numpy,cryptography,pypdf,pptx

def main():
    base=os.getenv('SMOKE_URL','http://127.0.0.1:8787')
    for _ in range(60):
        try:
            with urllib.request.urlopen(base+'/api/context/health',timeout=2) as r:
                assert json.load(r)['status']=='ok'
            break
        except Exception:time.sleep(1)
    else:raise RuntimeError('container_not_ready')
    client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    body=urllib.parse.urlencode({'username':os.environ['INTICLASS_ADMIN_USER'],'password':os.environ['INTICLASS_ADMIN_PASSWORD']}).encode()
    with client.open(base+'/api/context/login',body) as r:assert json.load(r)['role']=='admin'
    with client.open(base+'/api/context/classrooms') as r:assert isinstance(json.load(r),list)
    with client.open(base+'/app') as r:assert b'<html' in r.read().lower()
    try:urllib.request.urlopen(base+'/api/context/classrooms')
    except urllib.error.HTTPError as e:assert e.code==401
    else:raise AssertionError('anonymous_data_access')
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=64x64:d=0.1','-f','null','-'],check=True)
    assert cv2.imdecode(cv2.imencode('.jpg',numpy.zeros((16,16,3),dtype=numpy.uint8))[1],1).shape==(16,16,3)
    print(json.dumps({'container_smoke':'passed','authentication':True,'frontend':True,'opencv':cv2.__version__,'pyav':av.__version__,'ffmpeg':True}))

if __name__=='__main__':main()

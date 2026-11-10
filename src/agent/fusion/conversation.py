"""Owner-scoped persisted turns and exact-answer reuse over current evidence."""
import json,hashlib
from . import reporting

def turns(service,actor,run_id,last=None,limit=2000,allow_identity=False,archive=False):
    if not last:
        with service.repo.connection() as c:head=c.execute('SELECT report_id FROM fusion_conversation_heads WHERE owner=? AND run_id=?',(actor,run_id)).fetchone()
        if head:
            last=head[0]
            if not last:return []
    if not last:
        with service.repo.connection() as c:
            rows=c.execute('SELECT report_id,request,payload FROM fusion_reports WHERE owner=? ORDER BY created_at DESC, rowid DESC LIMIT 100',(actor,)).fetchall()
        last=next((r['report_id'] for r in rows if json.loads(r['request']).get('run_id')==run_id and (json.loads(r['request']).get('conversation') or json.loads(r['payload']).get('output_mode')!='report')),None)
    result=[];seen=set()
    while last and last not in seen and len(result)<limit:
        seen.add(last)
        with service.repo.connection() as c:r=c.execute('SELECT request FROM fusion_reports WHERE report_id=? AND owner=?',(last,actor)).fetchone()
        if not r:raise PermissionError('conversation_turn_access_denied')
        request=json.loads(r['request'])
        if request['run_id']!=run_id:raise ValueError('conversation_source_mismatch')
        if archive:
            try:report=reporting.historical(service,last,actor,allow_identity)
            except (ValueError,PermissionError):
                report={'report_id':last,'status':'stale','answer':None,'claims':[],'reason':''}
        else:report=reporting.load(service,last,actor,allow_identity)
        result.append({'question':request['question'],'report':report})
        last=request.get('previous_report_id')
    return list(reversed(result))


def threads(service,actor,run_id):
    """List saved branches; old conversations remain available after a new chat."""
    with service.repo.connection() as c:
        rows=c.execute('SELECT report_id,created_at,request,payload FROM fusion_reports WHERE owner=? AND json_extract(request,?)=? ORDER BY created_at DESC,rowid DESC',(actor,'$.run_id',run_id)).fetchall()
    records={r['report_id']:{'request':json.loads(r['request']),'created_at':r['created_at']} for r in rows
             if json.loads(r['request']).get('conversation') or json.loads(r['payload']).get('output_mode')!='report'}
    parents={r['request'].get('previous_report_id') for r in records.values()}
    result=[]
    for rid,record in records.items():
        if rid in parents:continue
        current=rid;seen=set();title=record['request']['question']
        while current in records and current not in seen:
            seen.add(current);entry=records[current];title=entry['request']['question'];current=entry['request'].get('previous_report_id')
        result.append({'last_report_id':rid,'title':title[:90],'turn_count':len(seen),'updated_at':record['created_at']})
    return result


def select_head(service,actor,run_id,last=None):
    with service.repo.connection() as c:
        if last:
            record=c.execute('SELECT request FROM fusion_reports WHERE report_id=? AND owner=?',(last,actor)).fetchone()
            if not record:raise PermissionError('conversation_turn_access_denied')
            if json.loads(record[0])['run_id']!=run_id:raise ValueError('conversation_source_mismatch')
        c.execute('INSERT OR REPLACE INTO fusion_conversation_heads VALUES (?,?,?)',(actor,run_id,last or ''))
    return {'saved':True,'last_report_id':last}

def set_head(service,actor,request,report):
    if request.conversation:
        with service.repo.connection() as c:c.execute('INSERT OR REPLACE INTO fusion_conversation_heads VALUES (?,?,?)',(actor,request.run_id,report['report_id']))

def history(service,actor,request,allow_identity=False):
    if not request.previous_report_id:return []
    values=turns(service,actor,request.run_id,request.previous_report_id,6,allow_identity,archive=True)
    def shown_answer(report):
        paragraphs=[p['text'] for p in report.get('narrative',[]) if p.get('analysis_section') in {'analysis','recommendations'} or p.get('heading','').startswith(('AI分析','AI综合','教学建议'))]
        return '\n\n'.join(paragraphs)[:6000] if paragraphs else report.get('answer')
    return [{'question':v['question'],'answer':shown_answer(v['report']),'scope':v['report'].get('query_plan',{}),
             'status':v['report']['status'],'context_scope':'prior_conversation_not_current_evidence'}
            for v in values if v['report']['status'] in {'current','historical_snapshot'}]

def key(request,query,provider,snapshot,conversation):
    config=getattr(provider,'config',{})
    model_digest=None
    if provider.__class__.__name__=='TextModel' and not getattr(provider,'transport',None):
        try:
            import httpx
            with httpx.Client(trust_env=False,follow_redirects=False,timeout=2) as c:
                r=c.get(provider.url+'/api/tags');r.raise_for_status()
                model_digest=next((m.get('digest') for m in r.json().get('models',[]) if m['name']==provider.model),None)
        except (httpx.HTTPError,ValueError):model_digest='unavailable'
    # Secret is hashed as part of settings fingerprint, never written verbatim.
    identity=hashlib.sha256(json.dumps({'model':provider.model,'model_digest':model_digest,'url':getattr(provider,'url',None),'config':config,'test_transport':id(provider.transport) if getattr(provider,'transport',None) else None},sort_keys=True).encode()).hexdigest()
    from src.inference.classroom_writer import PROMPT_VERSION
    value={'question':request.question.strip(),'plan':query.model_dump(),'conversation':request.conversation,'use_llm':request.use_llm,'runtime':identity,'snapshot':snapshot,'history':conversation,'writer':PROMPT_VERSION}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def cached(service,actor,fingerprint,allow_identity):
    with service.repo.connection() as c:rows=c.execute("SELECT report_id,payload,request FROM fusion_reports WHERE owner=? AND status='current' ORDER BY created_at DESC,rowid DESC LIMIT 60",(actor,)).fetchall()
    for row in rows:
        value=json.loads(row['payload'])
        if json.loads(row['request']).get('use_llm') and value.get('narrative_generation',{}).get('status')!='success':continue
        if value.get('trace',{}).get('answer_cache',{}).get('key')!=fingerprint:continue
        current=reporting.load(service,row['report_id'],actor,allow_identity)
        if current['status']=='current':
            current['trace']['answer_cache'].update(hit=True,reused_report_id=row['report_id'],new_model_calls=0)
            current['trace']['answer_cache']['original_generation_timings']=dict(current['trace']['timings'])
            current['trace']['timings']['model_calls']=0
            return current
    return None

"""Owner-scoped persisted turns and exact-answer reuse over current evidence."""
import json,hashlib
from . import reporting
from .planning import make_scope_key

INVALIDATION_SUMMARY='该轮回答所依据的资料或关联已变化，旧结论已作废，不再作为当前依据。'


def _head_value(service,actor,scope_key):
    with service.repo.connection() as c:
        if scope_key.startswith('run:'):
            row=c.execute('SELECT report_id FROM fusion_conversation_heads WHERE owner=? AND run_id=?',(actor,scope_key[4:])).fetchone()
        else:
            row=c.execute('SELECT last_report_id FROM scoped_conversation_heads WHERE owner=? AND scope_key=?',(actor,scope_key)).fetchone()
    return row[0] if row else None


def head(service,actor,run_id=None,*,scope_key=None):
    return _head_value(service,actor,scope_key or make_scope_key(None,run_id)) or None


def _matches_scope(request,run_id,scope_key):
    if scope_key is None:return request.get('run_id')==run_id
    try:return make_scope_key(request.get('lesson_id'),request.get('run_id'))==scope_key
    except ValueError:return False


def turns(service,actor,run_id=None,last=None,limit=2000,allow_identity=False,archive=False,*,scope_key=None):
    if not last:
        last=_head_value(service,actor,scope_key or make_scope_key(None,run_id))
        if last=='':return []
    if not last:
        with service.repo.connection() as c:
            field,_,value=(scope_key or f'run:{run_id}').partition(':')
            rows=c.execute('''SELECT report_id,request,payload FROM fusion_reports
                            WHERE owner=? AND json_extract(request,?)=?
                            ORDER BY created_at DESC,rowid DESC LIMIT 100''',(actor,'$.'+field+'_id',value)).fetchall()
        last=next((r['report_id'] for r in rows if _matches_scope(json.loads(r['request']),run_id,scope_key) and (json.loads(r['request']).get('conversation') or json.loads(r['payload']).get('output_mode')!='report')),None)
    result=[];seen=set()
    while last and last not in seen and len(result)<limit:
        seen.add(last)
        with service.repo.connection() as c:r=c.execute('SELECT request FROM fusion_reports WHERE report_id=? AND owner=?',(last,actor)).fetchone()
        if not r:raise PermissionError('conversation_turn_access_denied')
        request=json.loads(r['request'])
        if not _matches_scope(request,run_id,scope_key):
            if scope_key is None and result and request.get('lesson_id')==legacy_lesson:
                break
            raise ValueError('conversation_source_mismatch')
        legacy_lesson=request.get('lesson_id')
        if archive:
            try:report=reporting.historical(service,last,actor,allow_identity)
            except (ValueError,PermissionError):
                report={'report_id':last,'status':'stale','answer':None,'claims':[],'reason':''}
        else:report=reporting.load(service,last,actor,allow_identity)
        result.append({'question':request['question'],'report':report})
        last=request.get('previous_report_id')
    return list(reversed(result))


def threads(service,actor,run_id=None,*,scope_key=None):
    """List saved branches; old conversations remain available after a new chat."""
    field,_,value=(scope_key or f'run:{run_id}').partition(':')
    with service.repo.connection() as c:
        rows=c.execute('SELECT report_id,created_at,request,payload FROM fusion_reports WHERE owner=? AND json_extract(request,?)=? ORDER BY created_at DESC,rowid DESC',(actor,'$.'+field+'_id',value)).fetchall()
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


def select_head(service,actor,run_id=None,last=None,*,scope_key=None):
    with service.repo.connection() as c:
        if last:
            record=c.execute('SELECT request FROM fusion_reports WHERE report_id=? AND owner=?',(last,actor)).fetchone()
            if not record:raise PermissionError('conversation_turn_access_denied')
            if not _matches_scope(json.loads(record[0]),run_id,scope_key):raise ValueError('conversation_source_mismatch')
        if scope_key and scope_key.startswith('lesson:'):
            c.execute('INSERT OR REPLACE INTO scoped_conversation_heads VALUES (?,?,?,?)',
                      (actor,scope_key,last or '',reporting.now()))
        else:
            c.execute('INSERT OR REPLACE INTO fusion_conversation_heads VALUES (?,?,?)',(actor,run_id,last or ''))
    return {'saved':True,'last_report_id':last}

def set_head(service,actor,request,report,*,scope_key=None):
    if not request.conversation:return
    scope_key=scope_key or make_scope_key(request.lesson_id,request.run_id)
    with service.repo.connection() as c:
        if not scope_key.startswith('run:'):
            c.execute('INSERT OR REPLACE INTO scoped_conversation_heads (owner,scope_key,last_report_id,updated_at) VALUES (?,?,?,?)',
                      (actor,scope_key,report['report_id'],reporting.now()))
        # Keep the run-indexed conversation endpoints working for video turns.
        legacy_run_id=scope_key[4:] if scope_key.startswith('run:') else request.run_id
        if legacy_run_id:
            c.execute('INSERT OR REPLACE INTO fusion_conversation_heads VALUES (?,?,?)',(actor,legacy_run_id,report['report_id']))

def history(service,actor,request,allow_identity=False):
    if not request.previous_report_id:return []
    # Revalidate dependencies via load(), never use the display-only historical view.
    values=turns(service,actor,last=request.previous_report_id,limit=6,allow_identity=allow_identity,
                 scope_key=make_scope_key(request.lesson_id,request.run_id))
    if request.lesson_id:
        person=request.roster_subject_id or request.subject_id
        # A thread can be reopened at another student. Its earlier narrative
        # remains human-readable, but must not become this student's context.
        def same_person(turn):
            report=turn['report']
            if report.get('status')!='current':return True
            query=report.get('query_plan',{})
            return (query.get('roster_subject_id') or query.get('subject_id'))==person
        values=[v for v in values if same_person(v)]
    def shown_answer(report):
        paragraphs=[p['text'] for p in report.get('narrative',[]) if p.get('analysis_section') in {'analysis','recommendations'} or p.get('heading','').startswith(('AI分析','AI综合','教学建议'))]
        return '\n\n'.join(paragraphs)[:6000] if paragraphs else report.get('answer')
    return [
        {'question':v['question'],'answer':shown_answer(v['report']),'scope':v['report'].get('query_plan',{}),
         'status':'current','context_scope':'prior_conversation_not_current_evidence'}
        if v['report'].get('status')=='current' else
        {'question':v['question'],'status':'stale','invalidation_summary':INVALIDATION_SUMMARY}
        for v in values
    ]

def key(request,query,provider,snapshot,conversation,coverage=None):
    config=getattr(provider,'config',{})
    model_digest=None
    if query.intent!='cognitive_boundary' and provider.__class__.__name__=='TextModel' and not getattr(provider,'transport',None):
        try:
            import httpx
            with httpx.Client(trust_env=False,follow_redirects=False,timeout=2) as c:
                r=c.get(provider.url+'/api/tags');r.raise_for_status()
                model_digest=next((m.get('digest') for m in r.json().get('models',[]) if m['name']==provider.model),None)
        except (httpx.HTTPError,ValueError):model_digest='unavailable'
    # Secret is hashed as part of settings fingerprint, never written verbatim.
    identity=hashlib.sha256(json.dumps({'model':provider.model,'model_digest':model_digest,'url':getattr(provider,'url',None),'config':config,'test_transport':id(provider.transport) if getattr(provider,'transport',None) else None},sort_keys=True).encode()).hexdigest()
    from src.inference.classroom_writer import PROMPT_VERSION
    value={'question':request.question.strip(),'plan':query.model_dump(),'conversation':request.conversation,'use_llm':request.use_llm,'runtime':identity,'snapshot':snapshot,'history':conversation,'writer':PROMPT_VERSION,'coverage':coverage}
    if query.intent=='cognitive_boundary':value['boundary_response_version']=3
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def cached(service,actor,fingerprint,allow_identity):
    with service.repo.connection() as c:rows=c.execute("SELECT report_id,payload,request FROM fusion_reports WHERE owner=? AND status='current' ORDER BY created_at DESC,rowid DESC LIMIT 60",(actor,)).fetchall()
    for row in rows:
        value=json.loads(row['payload'])
        if json.loads(row['request']).get('use_llm') and value.get('query_plan',{}).get('intent')!='cognitive_boundary' and value.get('narrative_generation',{}).get('status')!='success':continue
        if value.get('trace',{}).get('answer_cache',{}).get('key')!=fingerprint:continue
        current=reporting.load(service,row['report_id'],actor,allow_identity)
        if current['status']=='current':
            current['trace']['answer_cache'].update(hit=True,reused_report_id=row['report_id'],new_model_calls=0)
            current['trace']['answer_cache']['original_generation_timings']=dict(current['trace']['timings'])
            current['trace']['timings']['model_calls']=0
            return current
    return None

"""Additive reports in the existing context store; currentness before every export."""
import html
import json
import re
from pathlib import Path
from src.identity.store import uid,now
from .evidence import version
from .retrieval import retrieve

VERSION='fusion-relevance-v2'


def migrate(repo):
    with repo.connection() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS fusion_reports(report_id TEXT PRIMARY KEY,owner TEXT NOT NULL,created_at TEXT NOT NULL,parent_id TEXT,status TEXT NOT NULL,request TEXT NOT NULL,query_plan TEXT NOT NULL,snapshot TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS fusion_reports_owner ON fusion_reports(owner,created_at);
    CREATE TABLE IF NOT EXISTS fusion_report_audit(audit_id TEXT PRIMARY KEY,report_id TEXT NOT NULL,actor TEXT NOT NULL,operation TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS fusion_conversation_heads(owner TEXT NOT NULL,run_id TEXT NOT NULL,report_id TEXT NOT NULL,PRIMARY KEY(owner,run_id));
    ''')


def snapshot(service,retrieved,plan):
    from src.validation.evidence_chain import dependencies
    # Existing repository revision changes include permissions/mappings/documents.
    from src.context.product_scope import metadata
    owner=retrieved.get('product_owner')
    from src.inference.settings import configured
    writing_policy=version({p.name:p.read_text(encoding='utf-8') for p in (Path(__file__).parents[2]/'inference').glob('*.py')})
    return version({'writing_policy':writing_policy,'text_runtime_version':version(configured(service)),'product_scope':metadata(service,plan,owner) if owner else None,'evidence':sorted((e['evidence_id'],e['source_version']) for e in retrieved['evidence']),
        'source_file':retrieved['bundle']['run']['metadata']['source_sha256'] if retrieved['bundle'] else None,
        'policy':VERSION,'policy_code':version({p.name:p.read_text(encoding='utf-8') for p in Path(__file__).parent.glob('*.py')}),
        'scope':plan.model_dump(),'dependencies':dependencies(service,retrieved,plan)})


def save(service,report,request,plan,actor,snap,parent_id=None):
    report['report_id']=uid();report['created_at']=now();report['status']='current';report['parent_id']=parent_id
    with service.repo.connection() as c:
        c.execute('INSERT INTO fusion_reports VALUES (?,?,?,?,?,?,?,?,?)',(report['report_id'],actor,report['created_at'],parent_id,'current',json.dumps(request.model_dump()),json.dumps(plan.model_dump()),snap,json.dumps(report,ensure_ascii=False)))
        c.execute('INSERT INTO fusion_report_audit VALUES (?,?,?,?,?,?)',(uid(),report['report_id'],actor,'created',now(),json.dumps({'parent_id':parent_id,'snapshot':snap})))
    return report


def load(service,rid,actor,allow_identity=False):
    from .planning import ReviewRequest,plan as rebuild_plan
    with service.repo.connection() as c:r=c.execute('SELECT * FROM fusion_reports WHERE report_id=?',(rid,)).fetchone()
    if not r:raise ValueError('fusion_report_not_found')
    if r['owner']!=actor:raise PermissionError('fusion_report_access_denied')
    stale=r['status']=='stale'
    invalid=[]
    if not stale:
        try:
            from .roster_scope import build_plan
            _,plan=build_plan(service,ReviewRequest.model_validate_json(r['request']),actor)
            fresh=retrieve(service,plan,actor,allow_identity)
            from src.validation.evidence_chain import dependencies,changed
            invalid=changed(json.loads(r['payload']).get('dependency_manifest',[]),dependencies(service,fresh,plan))
            stale=snapshot(service,fresh,plan)!=r['snapshot']
        except (ValueError,PermissionError,OSError) as error:
            stale=True;invalid=[{'dependency_id':'source_or_permission','reason':str(error)}]
    if stale:
        if r['status']!='stale':
            with service.repo.connection() as c:
                c.execute("UPDATE fusion_reports SET status='stale' WHERE report_id=?",(rid,))
                c.execute('INSERT INTO fusion_report_audit VALUES (?,?,?,?,?,?)',(uid(),rid,actor,'stale_detected',now(),json.dumps({'invalid_dependencies':invalid})))
        elif not invalid:
            with service.repo.connection() as c:
                last=c.execute("SELECT payload FROM fusion_report_audit WHERE report_id=? AND operation='stale_detected' ORDER BY created_at DESC LIMIT 1",(rid,)).fetchone()
            invalid=json.loads(last[0]).get('invalid_dependencies',[]) if last else []
        return {'report_id':rid,'status':'stale','answer':None,'claims':[], 'invalid_dependencies':invalid,'reason':'证据、授权、归属、轨迹复核、时间映射或规则版本已变化；旧结论已隐藏，请重算。'}
    return json.loads(r['payload'])


def historical(service,rid,actor,allow_identity=False):
    """Explicit immutable snapshot view for human comparisons, never current facts."""
    with service.repo.connection() as c:r=c.execute('SELECT owner,payload FROM fusion_reports WHERE report_id=?',(rid,)).fetchone()
    if not r:raise ValueError('fusion_report_not_found')
    if r['owner']!=actor:raise PermissionError('fusion_report_access_denied')
    report=json.loads(r['payload'])
    if not report:raise PermissionError('historical_source_deleted_or_redacted')
    evidence=report.get('audit',{}).get('retrieved_evidence',report.get('evidence',[]))
    for e in evidence:
        did=e['provenance'].get('document_id')
        if did:service.repo.document(did,actor)
        if e['evidence_type']=='identity_binding' and not allow_identity:raise PermissionError('identity_history_requires_review_role')
    report.update(status='historical_snapshot',historical_warning='历史策略快照，仅供对照审查；不代表当前有效结论，不运行模型或修改旧记录。')
    return report


from .answers import assemble


def markdown(report):
    if report['status']!='current':raise ValueError('stale_report_cannot_export_current_claims')
    def safe(s):return re.sub(r'([\\`*_{}\[\]()#+!|])',r'\\\1',html.escape(str(s))).replace('\n',' ')
    if report.get('narrative'):
        rows=['# 课迹 LessonTrace 复盘报告',''];registry={e['evidence_id']:e for e in report['evidence']};used=[]
        for paragraph in report['narrative']:
            rows+=['## '+safe(paragraph['heading']),'',safe(paragraph['text']),'']
            for ref in paragraph['evidence_refs']:
                if ref not in used:used.append(ref)
            if paragraph['evidence_refs']:rows+=['来源：'+', '.join('['+str(used.index(ref)+1)+']' for ref in paragraph['evidence_refs']),'']
        rows+=['## 引用资料与回放索引','']
        for index,ref in enumerate(used,1):
            e=registry[ref];p=e['provenance'];rows.append(f'[{index}] '+safe(p.get('title') or e['content'][:80])+(f'；来源时间 {e["time_scope"]} 秒' if e['time_scope'] else '')+'；引用ID '+safe(ref))
        rows+=['','详细模型状态、裁决与完整审计保存在同一报告的 JSON 导出中。']
        return '\n'.join(rows)+'\n'
    rows=['# IntiClass 课堂复盘报告','',safe(report['answer']),'',f"范围：{safe(report['classroom'])}"]
    if report.get('recognition_records'):
        rows+=['','## 行为识别记录','']
        for record in report['recognition_records']:
            rows.append('- '+safe(record['time_label'])+' / 识别状态：'+safe(record['recognition_state'])+' / 可靠性：'+safe(record['reliability'])+' / '+safe(record['source_label']))
            rows.append('  引用：'+', '.join(safe(r) for r in record['evidence_refs']))
        rows+=['','可靠性为当前模型与证据检查提示，不是实测识别准确率。']
    rows+=['','## 可追溯声明','']
    evidence={e['evidence_id']:e for e in report['evidence']}
    for claim in report['claims']:
        rows.append(f"- **{claim['final_claim_status']}** [{safe(claim['scope'])}] {safe(claim['statement'])}")
        rows.append('  引用：'+', '.join(safe(r) for r in claim['evidence_refs']))
    for section in report.get('sections',[]):
        rows+=['','## '+safe(section['heading']),'']
        rows +=['- '+safe(c['statement'])+' 引用：'+', '.join(safe(r) for r in c['evidence_refs']) for c in section['claims']]
        if not section['claims']:rows.append('参见范围、待复核、限制或来源索引；缺失项不自动补全为事实。')
    rows+=['','## 待复核与冲突','']
    for risk in report['review_queue']+report['conflicts']:rows.append('- '+safe(risk['kind'])+'：'+safe(risk['reason']))
    rows+=['','## 来源索引','']
    for e in evidence.values():rows.append('- '+safe(e['evidence_id'])+' / '+safe(e['evidence_type'])+' / '+safe(e['source_version']))
    if report.get('additional_claims'):
        rows+=['','<details><summary>附录：更多相关事实（非直接回答）</summary>','']
        rows +=['- '+safe(c['statement'])+' 引用：'+', '.join(safe(r) for r in c['evidence_refs']) for c in report['additional_claims']]
        rows+=['','</details>']
    rows+=['','完整审计（含完整规范目录、模型候选、拒收理由和版本）见JSON导出。']
    return '\n'.join(rows)+'\n'

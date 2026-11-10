import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Report, Evidence } from "../models/backend";
import { toLocal, observationText } from "../models/view";
import { useSession } from "../state/session";
import { getScope } from "../api/jobs";
import { getPassage } from "../api/context";
import { StatusChip } from "./EvidencePanel";
import {materialNames} from '../api/materials';
export function EvidenceCitation({ e, runId }: { e: Evidence; runId: string }) {
  const sourceNames:Record<string,string>={teacher_plan:'教案',teacher_report:'教师记录',teaching_activity:'教学活动',person_track:'人物轨迹',vlm_observation:'AI画面记录',model_discovered_event:'AI类别建议',human_confirmed_event:'教师复核',knowledge_rule:'分析规则',dataset_annotation:'原始标注'};
  const s = useSession(),
    nav = useNavigate(),
    [text, setText] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function open() {
    setBusy(true);
    setError("");
    try {
      if (
        ["teacher_plan", "teacher_report", "teaching_activity"].includes(
          e.evidence_type,
        )
      ) {
        const id =
          typeof e.provenance.passage_ref === "string"
            ? e.provenance.passage_ref.replace(/^text:/, "")
            : e.source_id;
        const p = await getPassage(id);
        setText(p.content);
      } else if (e.time_scope) {
        const rid =
          typeof e.provenance.run_id === "string" ? e.provenance.run_id : runId;
        const scope = await getScope(rid);
        const t = toLocal(
          e.time_scope[0],
          scope.start,
          scope.end - scope.start,
        );
        const matching = s.data.jobs.find((j) => j.run_id === rid);
        if (!matching)
          throw Error(
            "该历史来源尚未载入当前分析任务，不能跳转到另一段录像冒充证据。",
          );
        if (s.data.job?.run_id !== rid) await s.loadJob(matching.job_id);
        const tid = e.subject_ref?.startsWith("track:")
          ? Number(e.subject_ref.split(":").pop())
          : typeof e.provenance.track_id === "number"
            ? e.provenance.track_id
            : 0;
        nav("/review?t=" + t + (tid ? "&track=" + tid : ""));
      } else setText(e.content);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="product-citation">
      <button
        className="btn btn--ghost btn--sm"
        disabled={busy}
        onClick={() => void open()}
      >
        {busy ? "正在定位……" : e.time_scope ? "回放视频证据" : "查看原文或来源"}{" "}
        · {(typeof e.provenance.source_kind==='string'&&materialNames[e.provenance.source_kind])||sourceNames[e.evidence_type]||'来源资料'}
      </button>
      {text && <blockquote>{text}</blockquote>}
      {error && (
        <p className="upload__error" role="alert">
          {error}
        </p>
      )}
      <details>
        <summary>来源及范围</summary>
        <pre>{JSON.stringify(e, null, 2)}</pre>
      </details>
    </div>
  );
}
export function ReportContent({ report }: { report: Report }) {
  if (report.status === "stale")
    return (
      <p className="muted">这条历史消息的来源已撤回。您可以继续提问。</p>
    );
  const evidence = new Map(report.evidence.map((e) => [e.evidence_id, e]));
  const texts=report.evidence.filter(e=>['teacher_plan','teacher_report'].includes(e.evidence_type)&&e.content.trim().length>10).slice(0,2);
  if(report.narrative?.length){
   const full=report.output_mode==='report';
   const hasAnalysis=report.narrative.some(p=>p.heading.startsWith('AI分析')||p.heading==='AI综合分析'||p.heading.startsWith('教学建议'))&&['success','partial_success'].includes(report.narrative_generation?.status||'success');
   const background=(p:NonNullable<typeof report.narrative>[number])=>['结合教学与学习资料','结论边界'].includes(p.heading)||(p.heading==='依据与限制'&&!report.conflicts.length);
   return <div className="narrative-report">
   {!full&&!hasAnalysis&&<p className="msg__lead" data-testid="direct-answer">{report.answer}</p>}
   {report.product_scope&&<p className="paper__sub">{report.product_scope.classroom_name}{report.product_scope.subject_name?' · 资料对象：'+report.product_scope.subject_name:''}</p>}
   {report.narrative_generation?.status==='partial_success'&&<p className="muted">部分段落未通过证据校验，已保留有效内容。详情中可查看生成记录。</p>}
   {report.narrative_generation?.status&&/fallback|busy|failed/.test(report.narrative_generation.status)&&<p className="muted" role="status">本次模型未完成有效回答，以下先展示已有证据整理。您可以稍后重试。</p>}
   {report.narrative.filter(p=>full||(!['本次复盘范围','可观察行为'].includes(p.heading)&&!background(p))).map((p,i)=><section className="narrative-section" key={i}>{(full||!['AI综合分析','教学建议与跟进'].includes(p.heading))&&<h3>{p.heading.replace(/^AI分析：/,'')}</h3>}<p data-testid={(full||hasAnalysis)&&i===0?'direct-answer':undefined}>{p.text}</p>{p.evidence_refs.length>0&&<details className="narrative-sources"><summary>查看本段依据（{p.evidence_refs.length}）</summary>{p.evidence_refs.map(ref=>{const e=evidence.get(ref);return e?<EvidenceCitation key={ref} e={e} runId={report.classroom.run_id}/>:<p key={ref} role="alert">引用已不可用</p>;})}</details>}</section>)}
   {!full&&report.narrative.some(background)&&<details className="narrative-sources"><summary>本轮参考资料与适用范围</summary>{report.narrative.filter(background).map((p,i)=><section key={i}><h4>{p.heading}</h4><p>{p.text}</p>{p.evidence_refs.map(ref=>{const e=evidence.get(ref);return e?<EvidenceCitation key={ref} e={e} runId={report.classroom.run_id}/>:null;})}</section>)}</details>}
   <details className="narrative-audit"><summary>查看完整证据、原始模型裁决与运行记录</summary><p>文字组织不改变原始模型状态，也不将教师补充升级为视频事实。</p><pre>{JSON.stringify(report,null,2)}</pre></details>
  </div>;}
  return (
    <>
      {report.product_scope&&<p>班级：{report.product_scope.classroom_name}{report.product_scope.subject_name?` · 学生：${report.product_scope.subject_name}`:" · 课堂观察报告"}</p>}
      <p className="msg__lead" data-testid="direct-answer">
        {report.answer}
      </p>
      {texts.length>0&&<section aria-label="引用的教学资料"><h3 className="paper__h3">本次引用的教学资料</h3>{texts.map(e=><EvidenceCitation key={e.evidence_id} e={e} runId={report.classroom.run_id}/>)}</section>}
      <p className="paper__sub">
        来源范围：{report.classroom.time_range?.join("—")}秒（登记来源全局时间）
        · 单段片段，不代表整节课
      </p>
      {!!report.recognition_records?.length&&<section aria-label="行为识别记录">
        <h3 className="paper__h3">行为识别记录</h3>
        {report.recognition_records.map((record,i)=><article key={i} className="card card__body">
          <div className="claim-list__head"><strong>识别状态：{observationText(record.recognition_state)}</strong><span className="badge badge--observation" title={record.reliability_basis}>可靠性：{record.reliability}</span></div>
          <p>{record.time_label} · {record.source_label}</p>
          {record.evidence_refs.map(ref=>{const e=evidence.get(ref);return e?<EvidenceCitation key={ref} e={e} runId={report.classroom.run_id}/>:<p role="alert" key={ref}>引用未载入</p>})}
        </article>)}
        <p className="faint">可靠性是当前模型与证据检查的提示，不是经过测定的识别准确率。教师可在回放中修改记录。</p>
      </section>}
      <details open={!report.recognition_records?.length}><summary>分类建议、来源与裁决详情</summary>
      <div className="claim-list">
        {report.claims.map((c) => (
          <article key={c.fact_id} className="card card__body">
            <div className="claim-list__head">
              <StatusChip status={c.final_claim_status} />
              <span className="tag">{c.scope}</span>
            </div>
            <p>{c.statement}</p>
            <p className="muted">
              主体范围：{c.subject_ref || "来源或区域"}；时间：
              {c.time_scope?.join("—") || "未给出"}；只有所述作用范围得到支持。
            </p>
            {c.evidence_refs.map((ref) => {
              const e = evidence.get(ref);
              return e ? (
                <EvidenceCitation
                  key={ref}
                  e={e}
                  runId={report.classroom.run_id}
                />
              ) : (
                <p key={ref} role="alert">
                  引用未载入：{ref}。不能视为有效证据。
                </p>
              );
            })}
          </article>
        ))}
      </div>
      </details>
      {report.conflicts.length > 0 && (
        <section>
          <h3 className="paper__h3">识别分歧与核对事项</h3>
          {report.conflicts.map((r, i) => (
            <div className="note note--info" key={i}>
              <strong>{r.kind}</strong>
              <p>{r.reason}</p>
            </div>
          ))}
        </section>
      )}
      {report.review_queue.length>0&&<details><summary>可靠性说明与需核对记录（{report.review_queue.length}项）</summary>
        {report.review_queue.map((r,i)=><div className="note note--info" key={i}><p>{r.reason}</p><details><summary>原始风险标记</summary>{r.kind}</details></div>)}
      </details>}
      <h3 className="paper__h3">分析限制</h3>
      <ul>
        {report.limitations.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>
      <details>
        <summary>完整Agent执行轨迹、原始状态和证据版本</summary>
        <pre>{JSON.stringify(report, null, 2)}</pre>
      </details>
    </>
  );
}

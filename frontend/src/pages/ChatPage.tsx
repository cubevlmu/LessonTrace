import {useEffect,useRef,useState} from 'react';
import {z} from 'zod';
import {useSession} from '../state/session';
import {askReview} from '../api/review';
import {request} from '../api/client';
import {reportSchema,type Report} from '../models/backend';
import {memories,remember,deleteMaterial,detailSchema} from '../api/materials';
import {ReportContent} from '../components/ReportContent';
import {AnalysisScope,useAnalysisScope} from '../components/AnalysisScope';
type Turn={question:string;report:Report};
const savedChatsSchema=z.array(z.object({last_report_id:z.string(),title:z.string(),turn_count:z.number(),updated_at:z.string()}));
export function ChatPage(){
 const s=useSession(),scope=useAnalysisScope('',false);const [question,setQuestion]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const [turns,setTurns]=useState<Turn[]>([]),[pending,setPending]=useState(''),[memory,setMemory]=useState<z.infer<typeof detailSchema>[]>([]),[saveMemory,setSaveMemory]=useState(false),[useMemory,setUseMemory]=useState(true),[editing,setEditing]=useState<string|null>(null);
 const [savedChats,setSavedChats]=useState<z.infer<typeof savedChatsSchema>>([]);
 const historyGeneration=useRef(0);const dialog=useRef<HTMLDialogElement>(null),bottom=useRef<HTMLDivElement>(null),runRef=useRef(s.data.job?.run_id);runRef.current=s.data.job?.run_id;
 async function loadHistory(last?:string){
  const run=runRef.current;if(!run)return;const ticket=++historyGeneration.current;setLoading(true);setError('');
  try{const [values,notes,chats]=await Promise.all([request('fusion/conversation?run_id='+encodeURIComponent(run)+(last?'&last_report_id='+encodeURIComponent(last):''),z.array(z.object({question:z.string(),report:reportSchema}))),memories(run),request('fusion/conversations?run_id='+encodeURIComponent(run),savedChatsSchema)]);if(ticket===historyGeneration.current&&runRef.current===run){setTurns(values);setMemory(notes);setSavedChats(chats);}}
  catch(e){if(ticket===historyGeneration.current)setError(String(e));}finally{if(ticket===historyGeneration.current)setLoading(false);}
 }
 useEffect(()=>{setTurns([]);setSavedChats([]);setPending('');setError('');void loadHistory();return()=>{historyGeneration.current++;};},[s.data.job?.run_id]);
 async function selectChat(last?:string){
  const run=runRef.current;if(!run)return;setLoading(true);setError('');
  try{await request('fusion/conversation/select',z.object({saved:z.boolean()}),{run_id:run,last_report_id:last||null});setQuestion('');setPending('');await loadHistory(last);}
  catch(e){setError(String(e));setLoading(false);}
 }
 useEffect(()=>{bottom.current?.scrollIntoView?.({behavior:'smooth',block:'end'});},[turns.length,pending,busy]);
 async function send(q=question){
  const run=s.data.job?.run_id;if(!run||busy||!q.trim())return;setBusy(true);setPending(q);setQuestion('');setError('');setMessage('');
  try{
   const selection=scope.payload();let prompt=q;const command=/^(?:记住|记忆|补充课堂信息)\s*[:：]/.test(q);
   if(saveMemory||command){await remember({run_id:run,text:q.replace(/^(?:记住|记忆|补充课堂信息)\s*[:：]\s*/,''),subject_id:selection.roster_subject_id,track_id:selection.track_id,supersedes:editing});setMemory(await memories(run));setMessage('课堂补充已保存，后续回答会使用最新记录。');setSaveMemory(false);setEditing(null);if(command)prompt='结合刚保存的教师课堂补充与现有资料，说明当前可观察记录和教学建议。';}
   const previous=turns[turns.length-1]?.report;
   const report=await askReview({run_id:run,conversation:true,previous_report_id:previous?.status!=='stale'?previous?.report_id:null,question:prompt,...selection,use_classroom_memory:useMemory,use_llm:true,allow_verification:false,budget_seconds:180,max_model_calls:2});
   if(runRef.current!==run)return;s.setReport(report);setTurns(t=>[...t,{question:q,report}]);setPending('');void request('fusion/conversations?run_id='+encodeURIComponent(run),savedChatsSchema).then(setSavedChats).catch(()=>{});
  }catch(e){if(runRef.current===run){setError(String(e));setQuestion(q);setPending('');}}finally{setBusy(false);}
 }
 async function remove(id:string){if(!window.confirm('撤销这条课堂补充？相关旧报告会失效。'))return;try{await deleteMaterial(id);setMemory(await memories(s.data.job!.run_id!));setMessage('已撤销补充，后续分析使用最新资料。');}catch(e){setError(String(e));}}
 return <div className="page page--qa chat-workspace"><header className="chat-workspace__header"><div><h1 className="page__title">课堂助手</h1><p className="page__meta">结合课堂观察、作业与教学资料，继续交流。</p></div><div className="product-actions"><button className="btn btn--secondary" onClick={()=>dialog.current?.showModal()}>范围与资料</button><button className="btn btn--primary" disabled={busy||loading||s.restoring||!s.data.job?.run_id} onClick={()=>void selectChat()}>新对话</button></div></header>
 <div className="chat-workspace__body"><aside className="chat-history" aria-label="历史对话"><h2>历史对话</h2><p className="muted">自动保存，可随时继续</p>{savedChats.map(chat=><button key={chat.last_report_id} disabled={busy||loading} className={'chat-history__item'+(turns[turns.length-1]?.report.report_id===chat.last_report_id?' is-active':'')} onClick={()=>void selectChat(chat.last_report_id)}><strong>{chat.title}</strong><small>{chat.turn_count}轮 · {new Date(chat.updated_at).toLocaleDateString('zh-CN')}</small></button>)}{!savedChats.length&&<p className="muted">发送问题后，记录会保存在这里。</p>}</aside><div className="chat-workspace__main">
 <section className="conversation" aria-label="课堂对话"><div className="conversation__messages" aria-live="polite">{loading&&<p role="status">正在恢复对话…</p>}{!loading&&!turns.length&&!pending&&<div className="conversation__welcome"><div className="conversation__symbol">课迹</div><h2>今天想了解课堂中的什么？</h2><p>可以询问某个人的行为、比较作业与课堂安排，或生成有依据的复盘。</p><div className="conversation__starters">{['结合教案和作业，总结本段课堂的观察与建议。','当前人物有哪些可观察行为？','接下来教师可以重点跟进什么？'].map(q=><button className="card" key={q} disabled={s.restoring||!s.data.job?.run_id} onClick={()=>void send(q)}>{q}</button>)}</div></div>}
 {turns.map((t,i)=><article className="conversation__turn" key={t.report.report_id+':'+i}><div className="conversation__question"><span>你</span><p>{t.question}</p></div><div className="conversation__answer"><span className="conversation__assistant">课迹</span><div><ReportContent report={t.report}/></div></div></article>)}{pending&&<div className="conversation__question"><span>你</span><p>{pending}</p></div>}{busy&&<p className="conversation__thinking" role="status">正在阅读课堂背景与相关资料…</p>}{error&&<div role="alert" className="upload__error">{error}<button className="btn btn--ghost" disabled={busy} onClick={()=>void send()}>重试</button></div>}{message&&<p role="status" className="muted">{message}</p>}<div ref={bottom}/></div>
 <form className="conversation__composer" onSubmit={e=>{e.preventDefault();void send();}}><textarea aria-label="教师问题" rows={3} maxLength={1200} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="问问这节课，或输入“记住：……”补充背景" onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/><div className="conversation__composer-foot"><label className="classroom-check"><input type="checkbox" checked={saveMemory} onChange={e=>setSaveMemory(e.target.checked)}/>保存为课堂补充</label><span className="muted">Enter发送 · Shift+Enter换行</span><button className="btn btn--primary" disabled={busy||loading||s.restoring||!question.trim()||!s.data.job?.run_id}>发送</button></div></form></section></div></div>
 <dialog ref={dialog} className="classroom-dialog chat-context-dialog" aria-label="课堂范围与资料"><header><h2>课堂范围与资料</h2><button className="btn btn--ghost" onClick={()=>dialog.current?.close()}>关闭</button></header><AnalysisScope scope={scope}/><section><h3>教师课堂补充</h3><label className="classroom-check"><input type="checkbox" checked={useMemory} onChange={e=>setUseMemory(e.target.checked)}/>本次使用已保存的课堂补充</label>{memory.map(d=><article className="memory-card" key={d.document_id}><p>{d.passages.map(p=>p.content).join('\n')}</p>{s.canWrite&&<><button className="btn btn--ghost btn--sm" onClick={()=>{setQuestion(d.passages.map(p=>p.content).join('\n'));setSaveMemory(true);setEditing(d.document_id);scope.setTarget(d.scope?.subject_id?'student:'+d.scope.subject_id:d.scope?.track_id?'track:'+d.scope.track_id:'');dialog.current?.close();}}>修改</button><button className="btn btn--ghost btn--sm" onClick={()=>void remove(d.document_id)}>撤销</button></>}</article>)}{!memory.length&&<p className="muted">在对话中输入“记住：……”可明确保存教师补充。</p>}</section></dialog></div>;
}

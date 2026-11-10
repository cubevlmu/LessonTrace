import {useEffect,useRef,useState} from 'react';
import {Link,useNavigate} from 'react-router-dom';
import {z} from 'zod';
import {request} from '../api/client';
import {listClassrooms,getClassroom,type Classroom,type Student} from '../api/classrooms';
import {useSession} from '../state/session';
import {observationText,fmt} from '../models/view';
import {personTrackLabel,type IdentityLabel} from '../api/workbench';
import type {TrackGroup} from '../api/workbench';
import {canonicalTrack,memberTracks} from '../adapters/trackGroups';
const action=z.object({observed_action:z.string(),group_id:z.string().optional(),segment_id:z.number().optional(),observed_times:z.array(z.number()),evidence_refs:z.array(z.object({frame_id:z.string(),window_id:z.string(),frame_index:z.number(),local_time:z.number()}))}).passthrough();
const person=z.object({track_id:z.number(),profile_id:z.string(),segments:z.array(z.object({local_start:z.number(),local_end:z.number()})),action_groups:z.array(action),representative_frame:z.number(),representative_time:z.number(),portrait_url:z.string(),continuity_status:z.string()}).passthrough();
const profilesSchema=z.object({people:z.array(person),truncated_windows:z.boolean(),grouping_basis:z.string()});
type Person=z.infer<typeof person>;
type Shot={frame_index:number;local_time:number;portrait_url:string};
export function PersonProfiles({runId,currentTime,selectedTrack,labels,classroomId,version,onSelect,excludedTrackIds=[],trackGroups=[]}:{runId:string;currentTime:number;selectedTrack:number;labels:IdentityLabel[];classroomId?:string;version:number;excludedTrackIds?:number[];trackGroups?:TrackGroup[];onSelect:(id:number,time?:number)=>void}){
 const s=useSession(),dialog=useRef<HTMLDialogElement>(null);
 const [people,setPeople]=useState<Person[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[expanded,setExpanded]=useState(true),[message,setMessage]=useState('');
 const [draft,setDraft]=useState<Person|null>(null),[classes,setClasses]=useState<Classroom[]>([]),[cid,setCid]=useState(''),[students,setStudents]=useState<Student[]>([]),[sid,setSid]=useState(''),[name,setName]=useState(''),[shot,setShot]=useState<Shot|null>(null),[start,setStart]=useState(''),[end,setEnd]=useState(''),[note,setNote]=useState(''),[face,setFace]=useState(false),[authorized,setAuthorized]=useState(false),[consent,setConsent]=useState(''),[expiry,setExpiry]=useState(''),[busy,setBusy]=useState(false);
 const requestId=useRef('');
 const [demo,setDemo]=useState(false);
 useEffect(()=>{let active=true;setLoading(true);void request('workbench/people?run_id='+encodeURIComponent(runId),profilesSchema).then(r=>{if(active)setPeople(r.people);}).catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[runId,version]);
 useEffect(()=>{void listClassrooms().then(setClasses).catch(e=>setError(String(e)));},[]);
 useEffect(()=>{if(classroomId)setCid(classroomId);},[classroomId]);
 useEffect(()=>{setStudents([]);setSid('');if(cid)void getClassroom(cid).then(c=>setStudents(c.students)).catch(e=>setError(String(e)));},[cid]);
 useEffect(()=>{if(draft)dialog.current?.showModal();},[draft?.track_id]);
 function close(){dialog.current?.close();setDraft(null);}
 function open(p:Person){
  const seg=p.segments.find(v=>v.local_start<=p.representative_time&&p.representative_time<=v.local_end)||p.segments[0];
  setDemo(Boolean(classes.find(c=>c.classroom_id===cid)?.name.includes('演示')));setDraft(p);setShot({frame_index:p.representative_frame,local_time:p.representative_time,portrait_url:p.portrait_url});setStart(String(seg?.local_start||0));setEnd(String(seg?.local_end||0));setSid('');setName('');setNote('将该匿名轨迹登记到班级，行为仍保留原有证据状态');setFace(false);setAuthorized(false);setConsent('');setExpiry('');setError('');requestId.current=crypto.randomUUID();
 }
 async function currentShot(){if(!draft)return;setBusy(true);setError('');try{const r=await request(`workbench/people/${draft.track_id}/capture?run_id=${encodeURIComponent(runId)}&local_time=${currentTime}`,z.object({frame_index:z.number(),local_time:z.number(),portrait_url:z.string()}));setShot(r);const seg=draft.segments.find(v=>v.local_start<=r.local_time&&r.local_time<=v.local_end);if(seg){setStart(String(seg.local_start));setEnd(String(seg.local_end));}}catch(e){setError(String(e));}finally{setBusy(false);}}
 async function save(){
  if(!draft||!shot)return;setBusy(true);setError('');
  try{
   if(!start.trim()||!end.trim())throw Error('请填写有效时间范围');
   await request('workbench/registrations',z.object({subject_id:z.string(),binding_id:z.string(),identity_confirmed:z.boolean(),status:z.string()}).passthrough(),{request_id:requestId.current,classroom_id:cid,run_id:runId,track_id:draft.track_id,frame_index:shot.frame_index,local_start:Number(start),local_end:Number(end),subject_id:sid||null,display_name:name,note,authorized,consent_record:consent,expires_at:authorized?new Date(expiry).toISOString():'',enable_face_matching:face,label_origin:demo?'demo_setup':'operator'});
   close();setMessage('人物已加入班级，截图和该时段动作已关联。轨迹登记与独立身份确认分别保存。');window.dispatchEvent(new Event('lessontrace:identity-changed'));
  }catch(e){setError(String(e));}finally{setBusy(false);}
 }
 const aggregate=people.filter(p=>canonicalTrack(p.track_id,trackGroups)===p.track_id).map(p=>{
  const members=people.filter(other=>memberTracks(p.track_id,trackGroups).includes(other.track_id));
  return {...p,segments:members.flatMap(m=>m.segments),action_groups:members.flatMap(m=>m.action_groups)};
 });
 const visiblePeople=aggregate.filter(p=>!excludedTrackIds.includes(p.track_id));
 const shown=selectedTrack?aggregate.filter(p=>p.track_id===selectedTrack):expanded?visiblePeople:visiblePeople.slice(0,8);
 return <section className="card people-profiles"><div className="card__head"><h2 className="card__title">按人物归纳行为</h2><Link className="btn btn--ghost btn--sm" to={'/classrooms'+(classroomId?'?classroom='+classroomId:'')}>提前上传学生照片</Link></div><div className="card__body">
  <p className="muted">先按匿名轨迹整理动作，再登记到班级；未知姓名也能查看同一轨迹的多次观察。</p>
  {loading&&<p role="status">正在归纳人物记录…</p>}{error&&<p role="alert" className="upload__error">{error}</p>}{message&&<p role="status">{message}</p>}
  <div className="people-profile-grid">{shown.map(p=><article className="person-profile-card" key={p.profile_id}>
   <button className="person-profile-card__portrait" onClick={()=>onSelect(p.track_id,p.representative_time)}><img loading="lazy" src={p.portrait_url} alt={'人物'+p.track_id+'的实际录像截图'}/></button>
   <div><strong>{personTrackLabel(labels,p.track_id,currentTime)}</strong><p className="faint">{p.segments.length}段可见区间 · {p.continuity_status==='invalid'?'已有轨迹切换疑点':'轨迹连续性待核对'}</p>
    {p.action_groups.length?<ul>{p.action_groups.map(g=><li key={g.group_id||g.observed_action}><button className="person-action-link" title={g.observed_times.map(fmt).join('、')} onClick={()=>onSelect(p.track_id,g.observed_times[0])}>{observationText(g.observed_action)} · {g.observed_times.length}次观察</button></li>)}</ul>:<p className="muted">已有轨迹，尚无保存的动作观察</p>}
    <div className="product-actions"><button className="btn btn--ghost btn--sm" onClick={()=>onSelect(p.track_id)}>单独查看</button>{s.canWrite&&['admin','teacher'].includes(s.account?.role||'')&&<button className="btn btn--secondary btn--sm" onClick={()=>open(p)}>截图加入班级</button>}</div>
   </div>
  </article>)}</div>
  {!selectedTrack&&visiblePeople.length>8&&<button className="btn btn--ghost" onClick={()=>setExpanded(!expanded)}>{expanded?'收起人物列表':`展开其余${visiblePeople.length-8}条人物轨迹`}</button>}
  {!loading&&!people.length&&<p>人物跟踪完成后，这里会显示可归纳的匿名人物。</p>}
  {draft&&shot&&<dialog className="classroom-dialog person-registration" ref={dialog} aria-label="截图登记人物" onCancel={close}><header><h2>截图登记人物 {String(draft.track_id).padStart(3,'0')}</h2><button type="button" className="btn btn--ghost" onClick={close}>关闭</button></header>
   <form className="classroom-form" onSubmit={e=>{e.preventDefault();void save();}}>
    <div className="registration-preview"><img src={shot.portrait_url} alt="即将保存的人物截图"/><div><p>真实录像 {fmt(shot.local_time)} · 帧{shot.frame_index}</p><button type="button" className="btn btn--secondary" disabled={busy} onClick={()=>void currentShot()}>使用当前时刻画面</button><p className="faint">截图用于资料登记；不自动证明真实姓名。</p></div></div>
    <label>加入班级<select aria-label="登记班级" required value={cid} onChange={e=>setCid(e.target.value)}><option value="">选择班级</option>{classes.map(c=><option key={c.classroom_id} value={c.classroom_id}>{c.name}</option>)}</select></label>
    <label>关联人物<select aria-label="登记人物" value={sid} onChange={e=>setSid(e.target.value)}><option value="">添加新人物</option>{students.map(p=><option key={p.subject_id} value={p.subject_id}>{p.display_name}</option>)}</select></label>
    {!sid&&<label>姓名或演示名<input aria-label="登记显示名" required maxLength={120} value={name} onChange={e=>setName(e.target.value)}/></label>}
    <label>可见区间<select aria-label="登记可见区间" value={draft.segments.findIndex(seg=>Number(start)>=seg.local_start&&Number(end)<=seg.local_end)} onChange={e=>{const seg=draft.segments[Number(e.target.value)];if(seg){setStart(String(seg.local_start));setEnd(String(seg.local_end));}}}>{draft.segments.map((seg,i)=><option key={i} value={i}>{fmt(seg.local_start)}–{fmt(seg.local_end)}</option>)}</select></label>
    <div className="classroom-form__pair"><label>视频内起始秒<input aria-label="登记起始秒" type="number" min={0} step="any" required value={start} onChange={e=>setStart(e.target.value)}/></label><label>视频内结束秒<input aria-label="登记结束秒" type="number" min={0} step="any" required value={end} onChange={e=>setEnd(e.target.value)}/></label></div>
    <label>登记说明<textarea aria-label="登记说明" minLength={5} required value={note} onChange={e=>setNote(e.target.value)}/></label>
    <label className="classroom-check"><input type="checkbox" checked={demo} disabled={authorized} onChange={e=>setDemo(e.target.checked)}/>这是演示用轨迹标记，不代表真实学生身份确认</label>
    <details><summary>同时启用后续照片自动匹配（需实际授权）</summary><label className="classroom-check"><input type="checkbox" checked={authorized} onChange={e=>{setAuthorized(e.target.checked);if(e.target.checked)setDemo(false);if(!e.target.checked)setFace(false);}}/>已取得该人物资料登记及所选用途的实际授权</label>{authorized&&<><label>授权依据<textarea aria-label="截图登记授权依据" required minLength={5} value={consent} onChange={e=>setConsent(e.target.value)}/></label><label>授权到期<input aria-label="截图登记授权到期" type="datetime-local" required value={expiry} onChange={e=>setExpiry(e.target.value)}/></label><label><input type="checkbox" checked={face} onChange={e=>setFace(e.target.checked)}/>允许本地人脸特征提取与后续匹配</label></>}</details>
    <p className="faint">登记只适用于指定轨迹与时间段；不跨越轨迹中断，不自动确认动作类别或跨视频身份。</p>
    {error&&<p role="alert" className="upload__error">{error}</p>}<button className="btn btn--primary" disabled={busy||!cid||(!sid&&!name.trim())}>{busy?'正在保存…':'保存截图并加入班级'}</button>
   </form>
  </dialog>}
 </div></section>;
}

const historySchema=z.object({display_name:z.string(),identity_confirmed:z.boolean(),profiles:z.array(z.object({binding_id:z.string(),run_id:z.string(),track_id:z.number(),local_start:z.number(),local_end:z.number(),portrait_url:z.string(),action_groups:z.array(action)}))});
export function RegisteredTrackHistory({subjectId}:{subjectId:string}){
 const s=useSession(),nav=useNavigate();const [data,setData]=useState<z.infer<typeof historySchema>|null>(null),[error,setError]=useState('');
 useEffect(()=>{setData(null);setError('');void request(`workbench/students/${encodeURIComponent(subjectId)}/profiles`,historySchema).then(setData).catch(e=>setError(String(e)));},[subjectId]);
 async function open(run:string,tid:number,t:number){try{const job=s.data.jobs.find(j=>j.run_id===run);if(!job)throw Error('当前账户未载入该录像任务');await s.loadJob(job.job_id);nav('/review?track='+tid+'&t='+t);}catch(e){setError(String(e));}}
 async function revoke(id:string){const reason=window.prompt('撤销轨迹登记原因（至少5字）');if(!reason)return;try{await request(`workbench/registrations/${encodeURIComponent(id)}/revoke`,z.object({status:z.string()}),{reason});const updated=await request(`workbench/students/${encodeURIComponent(subjectId)}/profiles`,historySchema);setData(updated);window.dispatchEvent(new Event('lessontrace:identity-changed'));}catch(e){setError(String(e));}}
 return <div className="card__body">{error&&<p role="alert">{error}</p>}{data&&!data.profiles.length&&<p>这份演示资料尚未建立人物身份关联。可先在视频工作台查看匿名轨迹与动作记录。</p>}{data?.profiles.map(p=><article className="person-profile-card" key={p.binding_id}><img className="registered-profile-image" src={p.portrait_url} alt="登记时的真实轨迹截图"/><div><strong>{data.display_name} · 轨迹登记</strong><p>{fmt(p.local_start)}–{fmt(p.local_end)} · 人物{p.track_id}</p>{p.action_groups.map(g=><p key={g.group_id||g.observed_action}>{observationText(g.observed_action)} · {g.observed_times.map(fmt).join('、')}</p>)}<button className="btn btn--secondary btn--sm" onClick={()=>void open(p.run_id,p.track_id,p.local_start)}>查看人物与动作</button><button className="btn btn--ghost btn--sm" onClick={async()=>{try{const job=s.data.jobs.find(j=>j.run_id===p.run_id);if(!job)throw Error("请先载入该课堂");await s.loadJob(job.job_id);nav("/report?profile="+encodeURIComponent(subjectId));}catch(e){setError(String(e));}}}>生成此人物报告</button>{s.canWrite&&['admin','teacher'].includes(s.account?.role||'')&&<button className="btn btn--ghost btn--sm" onClick={()=>void revoke(p.binding_id)}>撤销登记关联</button>}<p className="faint">按视频轨迹归纳，未作为真实姓名或行为的独立人工确认。</p></div></article>)}</div>;
}

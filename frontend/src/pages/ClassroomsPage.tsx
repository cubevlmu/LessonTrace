import {useEffect,useRef,useState,type FormEvent,type ReactNode} from 'react';
import {Link,useNavigate,useSearchParams} from 'react-router-dom';
import * as api from '../api/classrooms';
import {useSession} from '../state/session';
import {StudentTimeline} from '../components/StudentTimeline';
import {videoTitle} from '../models/view';
import {videoUrl} from '../api/videos';
import {RegisteredTrackHistory} from '../components/PersonProfiles';
import type {Video,Job} from '../models/backend';

const tabs=[['students','学生名册'],['videos','课堂录像'],['jobs','分析记录'],['settings','班级设置']] as const;
const statusLabel:Record<string,string>={completed:'分析完成',running:'分析中',queued:'排队中',paused:'已暂停',failed:'需重试',cancelled:'已取消',interrupted:'已中断',pausing:'正在暂停',cancelling:'正在取消'};

function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:ReactNode}){
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const d=ref.current;if(d&&!d.open)d.showModal();return()=>d?.close();},[]);
 return <dialog ref={ref} className="classroom-dialog" aria-label={title} onCancel={onClose} onClick={e=>{if(e.target===e.currentTarget)onClose();}}><header><h2>{title}</h2><button type="button" className="btn btn--ghost" aria-label="关闭窗口" onClick={onClose}>关闭</button></header>{children}</dialog>;
}

function ClassForm({initial,busy,onSave}:{initial?:api.Classroom;busy:boolean;onSave:(body:{name:string;grade:string;term:string;description:string})=>void}){
 const [name,setName]=useState(initial?.name||''),[grade,setGrade]=useState(initial?.grade||''),[term,setTerm]=useState(initial?.term||''),[description,setDescription]=useState(initial?.description||'');
 return <form className="classroom-form" onSubmit={e=>{e.preventDefault();onSave({name,grade,term,description});}}>
  <label>班级名称<input aria-label="班级名称" value={name} onChange={e=>setName(e.target.value)} required maxLength={160} placeholder="例如：三年级语文演示班"/></label>
  <div className="classroom-form__pair"><label>年级<input aria-label="年级" value={grade} onChange={e=>setGrade(e.target.value)} maxLength={80}/></label><label>学期<input aria-label="学期" value={term} onChange={e=>setTerm(e.target.value)} maxLength={80}/></label></div>
  <label>备注<textarea aria-label="班级备注" value={description} onChange={e=>setDescription(e.target.value)} maxLength={1000} rows={3}/></label>
  <button className="btn btn--primary" disabled={busy||!name.trim()}>{busy?'正在保存…':initial?'保存班级设置':'创建班级'}</button>
 </form>;
}

function StudentForm({busy,onSave,initial}:{busy:boolean;onSave:(body:api.StudentInput)=>void;initial?:api.Student}){
 const [name,setName]=useState(initial?.display_name||''),[record,setRecord]=useState(''),[expiry,setExpiry]=useState(''),[authorized,setAuthorized]=useState(false),[face,setFace]=useState(false),[reference,setReference]=useState(false);
 function submit(e:FormEvent){e.preventDefault();onSave({display_name:name,consent_record:record,expires_at:reference?'':new Date(expiry).toISOString(),authorized:reference?false:authorized,allow_face_matching:reference?false:face,reference_only:reference});}
 return <form className="classroom-form" onSubmit={submit}>
  <label>学生姓名或演示名<input aria-label="学生姓名" value={name} onChange={e=>setName(e.target.value)} required maxLength={120}/></label>
  {!initial&&<label className="classroom-check"><input type="checkbox" checked={reference} onChange={e=>setReference(e.target.checked)}/>仅保存演示人物资料，不进行身份识别</label>}
  <label>{reference?'资料来源':'授权依据'}<textarea aria-label="授权依据" value={record} onChange={e=>setRecord(e.target.value)} required minLength={5} maxLength={4000} rows={3} placeholder={reference?'说明截图或示例资料来自哪里':'授权者、允许用途与依据'}/></label>
  {!reference&&<><label>授权有效期<input aria-label="授权有效期" type="datetime-local" value={expiry} onChange={e=>setExpiry(e.target.value)} required/></label><label className="classroom-check"><input type="checkbox" checked={authorized} onChange={e=>setAuthorized(e.target.checked)}/>已获得登记及保存照片的有效授权</label><label className="classroom-check"><input type="checkbox" checked={face} onChange={e=>setFace(e.target.checked)}/>同时授权本地人脸匹配（可选）</label></>}
  <button className="btn btn--primary" disabled={busy||(!reference&&!authorized)}>{busy?'正在保存…':initial?'保存登记授权':'添加并保存'}</button>
 </form>;
}

export function ClassroomsPage(){
 const s=useSession(),nav=useNavigate(),[params,setParams]=useSearchParams();
 const cid=params.get('classroom')||'',tab=tabs.some(t=>t[0]===params.get('tab'))?params.get('tab')!:'students';
 const cidRef=useRef(cid);cidRef.current=cid;
 const [classes,setClasses]=useState<api.Classroom[]>([]),[detail,setDetail]=useState<Awaited<ReturnType<typeof api.getClassroom>>|null>(null),[videos,setVideos]=useState<Video[]>([]),[selected,setSelected]=useState<api.Student|null>(null),[refs,setRefs]=useState<Awaited<ReturnType<typeof api.references>>>([]),[modal,setModal]=useState<'class'|'student'|'authorize'|null>(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[search,setSearch]=useState(''),[attach,setAttach]=useState(''),[profileTab,setProfileTab]=useState<'photos'|'timeline'>('photos');
 const canEdit=['admin','teacher'].includes(s.account?.role||'');
 async function loadClasses(){const result=await api.listClassrooms();setClasses(result);return result;}
 async function loadClass(id:string){const [d,v]=await Promise.all([api.getClassroom(id),api.classVideos(id)]);if(cidRef.current!==id)return;setDetail(d);setVideos(v.filter(x=>x.status==='active'));}
 async function task(work:()=>Promise<void>){setBusy(true);setError('');setMessage('');try{await work();}catch(e){setError(String(e));}finally{setBusy(false);}}
 useEffect(()=>{let alive=true;void loadClasses().then(rows=>{if(alive&&!params.get('classroom')&&rows.length)setParams({classroom:rows[0].classroom_id,tab:'students'},{replace:true});}).catch(e=>setError(String(e))).finally(()=>{if(alive)setLoading(false);});return()=>{alive=false;};},[]);
 useEffect(()=>{setSelected(null);setRefs([]);setDetail(null);setVideos([]);setSearch('');if(cid){setLoading(true);void loadClass(cid).catch(e=>{if(cidRef.current===cid)setError(String(e));}).finally(()=>{if(cidRef.current===cid)setLoading(false);});}},[cid]);
 async function choose(p:api.Student){setSelected(p);setRefs([]);setProfileTab('photos');setRefs(await api.references(p.subject_id));}
 function selectTab(next:string){setSelected(null);setParams({classroom:cid,tab:next});setMessage('');}
 const students=detail?.students||[],classJobs=s.data.jobs.filter(j=>videos.some(v=>v.video_id===j.video_id)),shown=students.filter(p=>p.display_name.includes(search.trim()));
 async function openVideo(video:Video,job?:Job){if(job){await s.loadJob(job.job_id);nav('/review');}else{s.selectVideo(video.video_id);nav('/?classroom='+encodeURIComponent(cid));}}
 const available=s.data.videos.filter(v=>v.status==='active'&&!videos.some(x=>x.video_id===v.video_id));
 return <div className="page page--classroom"><header className="page__head"><div><div className="page__eyebrow">班级工作空间</div><h1 className="page__title">班级管理</h1><p className="page__meta">管理学生照片、课堂录像与复盘记录。</p></div>{canEdit&&<button className="btn btn--primary" onClick={()=>setModal('class')}>创建班级</button>}</header>
  {error&&<p role="alert" className="upload__error">{error}<button className="btn btn--ghost btn--sm" onClick={()=>void task(async()=>{await loadClasses();if(cid)await loadClass(cid);})}>重试</button></p>}{message&&<p role="status" className="src-note">{message}</p>}
  <div className="classroom-layout"><aside className="classroom-list card" aria-label="班级列表"><div className="card__head"><h2 className="card__title">我的班级</h2><span className="muted">{classes.length}</span></div><div className="card__body">
   <label className="classroom-picker">选择班级<select aria-label="我的班级" value={cid} onChange={e=>{setError('');setParams({classroom:e.target.value,tab:'students'});}}><option value="" disabled>请选择班级</option>{classes.map(c=><option key={c.classroom_id} value={c.classroom_id}>{c.name} · {c.student_count||0}人</option>)}</select></label>
   {!classes.length&&!loading&&<p className="muted">还没有班级，先创建一个班级。</p>}
  </div></aside><div className="classroom-main">
  {loading&&<section className="card card__body" role="status">正在载入班级…</section>}
  {!loading&&!cid&&<section className="card card__body classroom-empty"><h2>从一个班级开始</h2><p>创建班级，添加学生照片，再上传课堂录像。</p>{canEdit&&<button className="btn btn--primary" onClick={()=>setModal('class')}>创建第一个班级</button>}</section>}
  {detail&&<><section className="classroom-hero card"><div><h2>{detail.name}</h2><p>{[detail.grade,detail.term].filter(Boolean).join(' · ')||'课堂工作空间'}</p></div><div className="classroom-counts"><span><strong>{students.length}</strong>人物资料</span><span><strong>{videos.length}</strong>课堂录像</span><span><strong>{classJobs.filter(j=>j.status==='completed').length}</strong>已完成分析</span></div></section>
   <div className="classroom-tabs" role="tablist" aria-label="班级内容">{tabs.map(([key,label])=><button role="tab" aria-selected={tab===key} id={'class-tab-'+key} aria-controls={'class-panel-'+key} key={key} onClick={()=>selectTab(key)}>{label}</button>)}</div>
   <section role="tabpanel" id={'class-panel-'+tab} aria-labelledby={'class-tab-'+tab}>
   {tab==='students'&&<><div className="classroom-toolbar"><label className="classroom-search"><span className="sr-only">查找学生</span><input aria-label="查找学生" placeholder="查找姓名或演示名" value={search} onChange={e=>setSearch(e.target.value)}/></label>{canEdit&&<button className="btn btn--primary" onClick={()=>setModal('student')}>添加学生</button>}</div>
    {!selected&&<div className="student-grid">{shown.map(p=><button className="student-tile card" key={p.subject_id} onClick={()=>void task(()=>choose(p))} aria-label={'查看'+p.display_name}>
     {p.photo_reference_id?<img src={api.photoUrl(p.subject_id,p.photo_reference_id)} alt={p.display_name+'的参考照片'} loading="lazy"/>:<div className="student-tile__placeholder">{p.display_name.slice(0,2)}</div>}
     <div className="student-tile__info"><strong>{p.display_name}</strong><span>{p.status==='reference_only'?(p.track_registration_count?'轨迹已登记':'演示人物 · 未作身份登记'):p.status==='active'?'已登记':'登记已撤销'} · {p.photo_count||0}张照片</span></div></button>)}</div>}
    {!selected&&!shown.length&&<div className="classroom-empty card card__body"><h3>{search?'没有找到匹配的学生':'还没有学生资料'}</h3><p>{search?'试试其他姓名。':'添加学生后，可以上传照片并查看其课堂记录。'}</p></div>}
    {selected&&<section className="card student-profile"><header className="card__head"><div><button className="btn btn--ghost btn--sm" onClick={()=>setSelected(null)}>返回名册</button><h2>{selected.display_name}</h2></div><span className="badge badge--observation">{selected.status==='reference_only'?'演示参考':'人物资料'}</span></header>
     <div className="classroom-tabs student-profile__tabs" role="tablist" aria-label="人物资料">{(['photos','timeline'] as const).map(key=><button key={key} role="tab" aria-selected={profileTab===key} onClick={()=>setProfileTab(key)}>{key==='photos'?'照片与资料':'个人时间线'}</button>)}</div>
     {profileTab==='photos'?<div className="card__body"><div className="student-photos">{refs.filter(r=>r.status==='active').map(r=><figure key={r.reference_image_id}><img src={api.photoUrl(selected.subject_id,r.reference_image_id)} alt={selected.display_name+'参考照片'}/>{canEdit&&<button className="btn btn--ghost btn--sm" disabled={busy} onClick={()=>{if(window.confirm('删除这张照片及其相关特征？'))void task(async()=>{await api.deletePhoto(selected.subject_id,r.reference_image_id);await choose(selected);await loadClass(cid);setMessage('照片已删除');});}}>删除照片</button>}</figure>)}</div>
      {canEdit&&<label className="student-upload">{busy?'正在保存…':'上传参考照片'}<input aria-label="参考照片" type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={busy} onChange={e=>{const files=Array.from(e.target.files||[]);e.target.value='';void task(async()=>{for(const f of files){if(f.size>8*1024*1024)throw Error('单张照片不得超过8 MiB');await api.uploadStudentPhoto(selected.subject_id,f);}await choose(selected);await loadClass(cid);setMessage('照片已保存');});}}/></label>}
      <p className="muted">{selected.status==='reference_only'?'同源视频截图仅作演示资料展示，不代表已确认身份；未提取人脸特征。':'照片保存在本地。登记照片后，在课堂录像中核对人物关联。'}</p>
      {canEdit&&<button className="btn btn--secondary" disabled={busy} onClick={()=>setModal('authorize')}>{selected.status==='reference_only'?'完成登记与匹配授权':'更新登记与匹配授权'}</button>}
      {canEdit&&<details><summary>修改或移除人物资料</summary><div className="product-actions"><button className="btn btn--secondary" disabled={busy} onClick={()=>{const n=window.prompt('修改显示名',selected.display_name);if(n)void task(async()=>{await api.editStudentName(selected.subject_id,n);setSelected({...selected,display_name:n});await loadClass(cid);});}}>修改显示名</button><button className="btn btn--ghost" disabled={busy} onClick={()=>{if(window.confirm('移除这份人物资料及其照片、身份关联？'))void task(async()=>{await api.revokeStudent(selected.subject_id);setSelected(null);await loadClass(cid);await loadClasses();});}}>移除资料</button></div></details>}
     </div>:<><RegisteredTrackHistory subjectId={selected.subject_id}/>{selected.status!=='reference_only'&&<StudentTimeline subjectId={selected.subject_id}/>}</>}</section>}
   </>}
   {tab==='videos'&&<><div className="classroom-toolbar"><p className="muted">{videos.length}段录像 · 点击进入分析与复盘</p><Link className="btn btn--primary" to={'/?classroom='+encodeURIComponent(cid)}>上传课堂录像</Link></div><div className="classroom-video-grid">{videos.map((v,index)=>{const job=classJobs.find(j=>j.video_id===v.video_id&&j.status==='completed')||classJobs.find(j=>j.video_id===v.video_id);return <article key={v.video_id} className="card classroom-video"><video preload="metadata" muted playsInline src={videoUrl(v.video_id)} aria-label={'录像预览'+(index+1)}/><div className="card__body"><h3>课堂片段 {index+1}</h3><p className="muted">{v.metadata.duration.toFixed(0)}秒 · {job?statusLabel[job.status]||job.status:'尚未分析'}</p><p className="faint">{videoTitle(v)}</p><button className="btn btn--primary" disabled={busy} onClick={()=>void task(()=>openVideo(v,job))}>{job?.status==='completed'?'查看课堂复盘':job?'查看分析进度':'开始分析'}</button></div></article>;})}</div>
    {!videos.length&&<div className="card card__body classroom-empty"><h3>尚未添加课堂录像</h3><p>上传视频后，将在这里保存每次分析和复盘。</p></div>}
    {canEdit&&available.length>0&&<details className="secondary-panel"><summary>关联已有录像</summary><div className="product-actions"><select aria-label="已有录像" value={attach} onChange={e=>setAttach(e.target.value)}><option value="">选择一段已上传录像</option>{available.map(v=><option key={v.video_id} value={v.video_id}>{videoTitle(v)}</option>)}</select><button className="btn btn--secondary" disabled={busy||!attach} onClick={()=>void task(async()=>{await api.linkVideo(cid,attach);await loadClass(cid);setAttach('');setMessage('录像已关联');})}>关联到本班</button></div></details>}
   </>}
   {tab==='jobs'&&<section className="card"><div className="card__head"><h2 className="card__title">分析记录</h2><button className="btn btn--ghost btn--sm" disabled={busy} onClick={()=>void task(async()=>{await s.refresh();await loadClass(cid);})}>刷新</button></div><div className="card__body">{classJobs.map(j=><div className="classroom-job" key={j.job_id}><div><strong>{videoTitle(videos.find(v=>v.video_id===j.video_id))}</strong><p className="muted">{statusLabel[j.status]||j.status} · 已处理{j.processed_windows||0}/{j.total_windows||0}个窗口</p></div><button className="btn btn--secondary" disabled={busy} onClick={()=>void task(async()=>{await s.loadJob(j.job_id);nav(j.status==='completed'?'/review':'/?classroom='+encodeURIComponent(cid));})}>{j.status==='completed'?'查看结果':'查看进度'}</button></div>)}{!classJobs.length&&<p className="muted">暂无分析记录，请先上传并分析课堂录像。</p>}</div></section>}
   {tab==='settings'&&<section className="card"><div className="card__head"><h2 className="card__title">班级信息</h2></div><div className="card__body">{canEdit?<ClassForm key={cid} initial={detail} busy={busy} onSave={body=>void task(async()=>{await api.editClassroom(cid,body.name,body);await loadClasses();await loadClass(cid);setMessage('班级设置已保存');})}/>:<p>当前账户只能查看班级资料。</p>}</div></section>}
   </section></>}
  </div></div>
  {modal==='class'&&<Modal title="创建班级" onClose={()=>setModal(null)}><ClassForm busy={busy} onSave={body=>void task(async()=>{const c=await api.createClassroom(body);await loadClasses();setParams({classroom:c.classroom_id,tab:'students'});setModal(null);setMessage('班级已创建');})}/></Modal>}
  {modal==='student'&&<Modal title="添加学生资料" onClose={()=>setModal(null)}><StudentForm busy={busy} onSave={body=>void task(async()=>{await api.addStudent(cid,body);await loadClass(cid);await loadClasses();setModal(null);setMessage('人物资料已保存，可点击卡片添加照片');})}/></Modal>}
  {modal==='authorize'&&selected&&<Modal title="登记与身份匹配授权" onClose={()=>setModal(null)}><StudentForm initial={selected} busy={busy} onSave={body=>void task(async()=>{await api.editStudent(selected.subject_id,body);await loadClass(cid);await loadClasses();const fresh=await api.getClassroom(cid);const person=fresh.students.find(p=>p.subject_id===selected.subject_id);if(person)await choose(person);setModal(null);setMessage('登记授权已保存。照片质量与匹配候选可在视频人物管理中查看。');})}/></Modal>}
 </div>;
}

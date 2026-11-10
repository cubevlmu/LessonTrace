import {useEffect,useRef,useState} from 'react';
import {z} from 'zod';
import {request} from '../api/client';
import {useSession} from '../state/session';
import {materialNames,materialStatus,listMaterials,getMaterial,deleteMaterial,detailSchema,type Material} from '../api/materials';
import {getPlaybackIdentities} from '../api/workbench';
import {getClassroom,type Student} from '../api/classrooms';
import {Icon} from '../components/Icon';

export function MaterialsPage(){
 const s=useSession();const uploadDialog=useRef<HTMLDialogElement>(null),viewDialog=useRef<HTMLDialogElement>(null);
 const [items,setItems]=useState<Material[]>([]),[selected,setSelected]=useState<z.infer<typeof detailSchema>|null>(null),[category,setCategory]=useState('all'),[search,setSearch]=useState('');
 const [title,setTitle]=useState(''),[text,setText]=useState(''),[file,setFile]=useState<File|null>(null),[source,setSource]=useState('teacher_plan'),[example,setExample]=useState(false),[inputMode,setInputMode]=useState<'file'|'text'>('file');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[linked,setLinked]=useState(true),[cid,setCid]=useState(''),[students,setStudents]=useState<Student[]>([]),[subject,setSubject]=useState(''),[transcript,setTranscript]=useState('');
 const [textAnchor,setTextAnchor]=useState(''),[videoAnchor,setVideoAnchor]=useState(''),[basis,setBasis]=useState(''),[confirmed,setConfirmed]=useState(false);
 async function load(){setItems(await listMaterials());}
 async function task(work:()=>Promise<void>){setBusy(true);setError('');try{await work();}catch(e){setError(String(e));}finally{setBusy(false);}}
 useEffect(()=>{void load().catch(e=>setError(String(e)));},[]);
 useEffect(()=>{setCid('');setStudents([]);if(s.data.job?.run_id)void getPlaybackIdentities(s.data.job.run_id).then(async r=>{if(r.classroom){setCid(r.classroom.classroom_id);setStudents((await getClassroom(r.classroom.classroom_id)).students);}}).catch(e=>setError(String(e)));},[s.data.job?.run_id]);
 async function open(d:Material){await task(async()=>{setSelected(await getMaterial(d.document_id));setTranscript('');viewDialog.current?.showModal();});}
 async function save(){
  await task(async()=>{
   const scope={run_id:linked?s.data.job?.run_id||null:null,classroom_id:linked?cid||null:null,subject_id:subject||null};let result;
   if(inputMode==='file'&&file){
    if(file.size>10*1024*1024)throw Error('单份资料不能超过10 MiB');const form=new FormData();form.append('title',title);form.append('source_type',source);form.append('file',file);form.append('is_example',String(example));form.append('scope',JSON.stringify(scope));
    result=await request('upload',z.object({document_id:z.string(),status:z.string()}),form);
   }else result=await request('documents',z.object({document_id:z.string(),status:z.string()}),{title,text,source_type:source,is_example:example,scope});
   await load();uploadDialog.current?.close();setMessage(['requires_ocr','requires_transcription'].includes(result.status)?'文件已保存，补录文字后可用于联合分析。':'资料已保存，可在问答或报告中选择引用。');
   setFile(null);setText('');setTitle('');
  });
 }
 const shown=items.filter(d=>(category==='all'||d.source_type===category)&&(!search||d.title.toLowerCase().includes(search.toLowerCase())));
 return <div className="page material-library"><header className="page__head"><div><h1 className="page__title">教学资料库</h1><p className="page__meta">集中管理教案、作业、批改与课堂补充，按需用于联合分析。</p></div>{s.canWrite&&<button className="btn btn--primary" onClick={()=>{setError('');uploadDialog.current?.showModal();}}>上传资料</button>}</header>
  {error&&<p role="alert" className="upload__error">{error}</p>}{message&&<p role="status">{message}</p>}
  <div className="library-toolbar"><input aria-label="搜索资料" className="product-input" placeholder="搜索资料名称" value={search} onChange={e=>setSearch(e.target.value)}/><select aria-label="筛选资料分类" value={category} onChange={e=>setCategory(e.target.value)}><option value="all">全部分类（{items.length}）</option>{Object.entries(materialNames).map(([key,name])=><option key={key} value={key}>{name}（{items.filter(d=>d.source_type===key).length}）</option>)}</select><span className="muted">{shown.length}份文件</span></div>
  <div className="material-grid">{shown.map(d=><button className="card material-card" key={d.document_id} onClick={()=>void open(d)}>
   <div className="material-card__icon"><Icon name="report" size={24}/><span>{(d.file_type||'文本').replace('.','').toUpperCase()}</span></div>
   <strong>{d.title}</strong><span className="badge badge--observation">{materialNames[d.source_type]||d.source_type}</span><p>{materialStatus[d.status]||d.status}</p>
   <small>{d.scope?.subject_id?'指定人物资料':d.scope?.run_id?'已关联课堂录像':'通用资料'}{d.is_example?' · 项目示例':''}</small>
  </button>)}</div>
  {!shown.length&&<div className="card card__body classroom-empty"><h2>这里还没有符合条件的资料</h2><p>上传教案、学生作业或教师说明后，即可从问答与报告中选用。</p></div>}

  <dialog ref={uploadDialog} className="classroom-dialog material-dialog" aria-label="上传教学资料"><header><h2>上传教学资料</h2><button type="button" className="btn btn--ghost" onClick={()=>uploadDialog.current?.close()}>关闭</button></header>
   <div className="classroom-tabs" role="tablist" aria-label="资料输入方式"><button role="tab" aria-selected={inputMode==='file'} onClick={()=>setInputMode('file')}>上传文件</button><button role="tab" aria-selected={inputMode==='text'} onClick={()=>setInputMode('text')}>输入文字</button></div>
   <form className="classroom-form" onSubmit={e=>{e.preventDefault();void save();}}>
    {inputMode==='file'&&<label>选择文件<input aria-label="教学资料文件" type="file" accept=".txt,.md,.pdf,.pptx,.docx,.png,.jpg,.jpeg,.webp" required onChange={e=>{const f=e.target.files?.[0]||null;setFile(f);if(f&&!title.trim())setTitle(f.name.replace(/\.[^.]+$/,''));}}/><small>支持文本、PDF、Word、PPT及图片，单份不超过10 MiB。图片和扫描页需补录文字。</small></label>}
    <label>资料标题<input aria-label="资料标题" required value={title} onChange={e=>setTitle(e.target.value)} maxLength={200}/></label>
    <label>文件分类<select aria-label="资料类型" value={source} onChange={e=>setSource(e.target.value)}>{Object.entries(materialNames).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select></label>
    {inputMode==='text'&&<label>资料内容<textarea aria-label="教学资料文本" rows={7} required value={text} onChange={e=>setText(e.target.value)}/></label>}
    {!!s.data.job?.run_id&&<label className="classroom-check"><input type="checkbox" checked={linked} onChange={e=>setLinked(e.target.checked)}/>关联当前课堂录像，供后续问答检索</label>}
    {students.length>0&&<label>资料所属人物<select aria-label="资料所属人物" value={subject} onChange={e=>setSubject(e.target.value)}><option value="">全班或通用资料</option>{students.map(p=><option key={p.subject_id} value={p.subject_id}>{p.display_name}</option>)}</select></label>}
    <label className="classroom-check"><input type="checkbox" checked={example} onChange={e=>setExample(e.target.checked)}/>项目示例资料，并非原课堂的真实材料</label>
    {error&&<p role="alert" className="upload__error">{error}</p>}<button className="btn btn--primary" disabled={busy||!(inputMode==='file'?file:text.trim())}>{busy?'正在保存…':'保存资料'}</button>
   </form>
  </dialog>

  <dialog ref={viewDialog} className="classroom-dialog material-dialog material-detail" aria-label="资料内容"><header><h2>{selected?.title||'资料内容'}</h2><button type="button" className="btn btn--ghost" onClick={()=>viewDialog.current?.close()}>关闭</button></header>
   {selected&&<><p className="muted">{materialNames[selected.source_type]} · {materialStatus[selected.status]||selected.status}</p>
    {['.png','.jpg','.jpeg','.webp'].includes(selected.file_type||'')&&<img className="material-image-preview" src={'/api/context/documents/'+selected.document_id+'/preview'} alt="已上传的原始资料图片"/>}
    <div className="material-passages">{selected.passages.map(p=><p key={p.passage_id}>{p.page_or_slide!==null&&<small>第{p.page_or_slide}页/幻灯片</small>}{p.content}</p>)}</div>
    {s.canWrite&&<><label>修改文件分类<select aria-label="修改文件分类" value={selected.source_type} onChange={e=>void task(async()=>{const next=await request('documents/'+selected.document_id+'/metadata',detailSchema,{source_type:e.target.value,title:selected.title,scope:selected.scope||{}},'PUT');setSelected(next);await load();setMessage('分类已更新，相关旧报告将重新检查依据。');})}>{Object.entries(materialNames).map(([key,name])=><option value={key} key={key}>{name}</option>)}</select></label>
    {['requires_transcription','requires_ocr','partial_requires_ocr'].includes(selected.status)&&<form className="classroom-form" onSubmit={e=>{e.preventDefault();void task(async()=>{setSelected(await request('documents/'+selected.document_id+'/transcript',detailSchema,{text:transcript}));await load();setMessage('文字已按教师补录来源保存，可用于分析。');});}}><label>补录可辨认的原文<textarea aria-label="资料补录文字" required value={transcript} onChange={e=>setTranscript(e.target.value)}/></label><button className="btn btn--secondary" disabled={busy||!transcript.trim()}>保存补录文字</button></form>}
    <details><summary>与视频建立精确时间对应（可选）</summary>{s.data.scope&&<form className="classroom-form" onSubmit={e=>{e.preventDefault();void task(async()=>{await request('alignments',z.object({link_id:z.string()}).passthrough(),{document_id:selected.document_id,lesson_id:'recording:'+s.data.videoId,clip_id:s.data.scope!.clip_id,time_reference:'lesson_elapsed',text_anchor:Number(textAnchor),video_global_anchor:Number(videoAnchor)+(s.data.scope?.start||0),basis,confirmation:confirmed});setMessage('时间映射已保存。');});}}><label>课程开始后秒数<input type="number" step="any" min="0" required value={textAnchor} onChange={e=>setTextAnchor(e.target.value)}/></label><label>视频内秒数<input type="number" step="any" min="0" required value={videoAnchor} onChange={e=>setVideoAnchor(e.target.value)}/></label><label>映射依据<textarea required minLength={5} value={basis} onChange={e=>setBasis(e.target.value)}/></label><label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已核对对应关系</label><button className="btn btn--secondary" disabled={busy||!confirmed}>保存时间对应</button></form>}</details>
    <button className="btn btn--ghost" disabled={busy} onClick={()=>{if(window.confirm('删除这份资料并使依赖报告失效？'))void task(async()=>{await deleteMaterial(selected.document_id);viewDialog.current?.close();setSelected(null);await load();setMessage('资料已删除，历史审计保留。');});}}>删除资料</button></>}
   </>}
  </dialog>
 </div>;
}

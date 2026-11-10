import {useEffect,useRef,useState} from 'react';
import {useSession} from '../state/session';
import {listMaterials,materialNames,type Material} from '../api/materials';
import {getPlaybackIdentities} from '../api/workbench';
import {getClassroom,type Student} from '../api/classrooms';

export function useAnalysisScope(initialTarget='',restoreReportScope=true){
 const s=useSession();const [target,setTarget]=useState(initialTarget),[documentIds,setDocumentIds]=useState<string[]>([]),[materials,setMaterials]=useState<Material[]>([]),[students,setStudents]=useState<Student[]>([]),[materialMode,setMaterialMode]=useState<'linked'|'selected'|'none'>('linked'),[error,setError]=useState(''),[start,setStart]=useState(''),[end,setEnd]=useState('');
 useEffect(()=>{void listMaterials().then(setMaterials).catch(e=>setError(String(e)));const run=s.data.job?.run_id;if(run)void getPlaybackIdentities(run).then(async r=>{setStudents(r.classroom?(await getClassroom(r.classroom.classroom_id)).students:[]);}).catch(e=>setError(String(e)));},[s.data.job?.run_id]);
 const applied=useRef(false);
 useEffect(()=>{if(!restoreReportScope||applied.current||s.restoring||s.data.report?.status!=='current')return;const p=s.data.report.query_plan as {document_ids?:string[];material_mode?:'linked'|'selected'|'none';roster_subject_id?:string;track_id?:number}|undefined;if(p){if(p.document_ids)setDocumentIds(p.document_ids);if(p.material_mode)setMaterialMode(p.material_mode);if(!initialTarget)setTarget(p.roster_subject_id?'student:'+p.roster_subject_id:p.track_id?'track:'+p.track_id:'');applied.current=true;}},[s.restoring,s.data.report,restoreReportScope]);
 function payload(){
  applied.current=true;
  const offset=s.data.scope?.start||0,a=start.trim()?Number(start)+offset:null,b=end.trim()?Number(end)+offset:null;
  if(a!==null&&!Number.isFinite(a)||b!==null&&!Number.isFinite(b)||a!==null&&b!==null&&a>b)throw Error('请输入合法且有序的视频内时间范围');
  return {track_id:target.startsWith('track:')?Number(target.slice(6)):null,roster_subject_id:target.startsWith('student:')?target.slice(8):null,subject_id:null,start:a,end:b,document_ids:documentIds,material_mode:materialMode};
 }
 return {target,setTarget,documentIds,setDocumentIds,materials,students,materialMode,setMaterialMode,error,start,setStart,end,setEnd,payload};
}

export function AnalysisScope({scope,showPerson=true}:{scope:ReturnType<typeof useAnalysisScope>;showPerson?:boolean}){
 const s=useSession();
 return <fieldset className="analysis-scope" disabled={s.restoring} style={{border:0,padding:0,margin:0,minWidth:0}}>{showPerson&&<label>分析对象<select aria-label="分析对象" value={scope.target} onChange={e=>scope.setTarget(e.target.value)}><option value="">当前课堂全部范围</option>{scope.students.length>0&&<optgroup label="班级人物">{scope.students.map(p=><option key={p.subject_id} value={'student:'+p.subject_id}>{p.display_name}</option>)}</optgroup>}<optgroup label="匿名人物轨迹">{s.data.tracks.map(t=><option key={t.track_id} value={'track:'+t.track_id}>人物{String(t.track_id).padStart(3,'0')}</option>)}</optgroup></select></label>}
  <div className="classroom-form__pair"><label>起始秒（可选）<input aria-label="分析起始秒" type="number" step="any" min={0} value={scope.start} onChange={e=>scope.setStart(e.target.value)}/></label><label>结束秒（可选）<input aria-label="分析结束秒" type="number" step="any" min={0} value={scope.end} onChange={e=>scope.setEnd(e.target.value)}/></label></div>
  <label>联合资料<select aria-label="联合资料方式" value={scope.materialMode} onChange={e=>scope.setMaterialMode(e.target.value as typeof scope.materialMode)}><option value="linked">使用当前课堂已关联资料</option><option value="selected">仅使用我选择的资料</option><option value="none">本次仅看视频与课堂记忆</option></select></label>
  {scope.materialMode==='selected'&&<div className="analysis-material-picker">{scope.materials.filter(d=>d.source_type!=='classroom_memory').map(d=><label key={d.document_id} className="analysis-material-choice"><input type="checkbox" checked={scope.documentIds.includes(d.document_id)} onChange={e=>scope.setDocumentIds(ids=>e.target.checked?[...ids,d.document_id].slice(0,10):ids.filter(id=>id!==d.document_id))}/><span><strong>{d.title}</strong><small>{materialNames[d.source_type]||'教学资料'}</small></span></label>)}{!scope.materials.length&&<p className="muted">还没有资料，可先到教学资料库上传。</p>}</div>}
  {scope.error&&<p role="alert">{scope.error}</p>}
 </fieldset>;
}

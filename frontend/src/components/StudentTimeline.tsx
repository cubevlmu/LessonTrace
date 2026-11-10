import {useEffect,useState} from 'react';
import {z} from 'zod';
import {useNavigate} from 'react-router-dom';
import {request} from '../api/client';
import {getScope} from '../api/jobs';
import {reportSchema} from '../models/backend';
import {useSession} from '../state/session';
import {actionLabel} from '../models/view';
const schema=z.object({display_name:z.string(),identity_status:z.string(),intervals:z.array(z.object({binding_id:z.string(),run_id:z.string(),track_id:z.number(),start:z.number(),end:z.number(),events:z.array(z.object({event_id:z.string(),action:z.string(),status:z.string(),times:z.array(z.number())}).passthrough())}).passthrough()),limitations:z.array(z.string())});
export function StudentTimeline({subjectId}:{subjectId:string}){
 const s=useSession(),nav=useNavigate();const [data,setData]=useState<z.infer<typeof schema>|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{setData(null);setError('');void request(`students/${subjectId}/timeline`,schema).then(setData).catch(e=>setError(String(e)));},[subjectId]);
 return <section className="card"><div className="card__head"><h2 className="card__title">个人行为时间线</h2></div><div className="card__body">{error&&<p role="alert">{error}</p>}{data&&!data.intervals.length&&<p>尚无有效人工身份关联，暂不能形成该学生的已确认行为报告。请先在视频工作台核对身份区间。</p>}{data?.intervals.map(i=><div key={i.binding_id}><p>Track {i.track_id} · 身份确认范围 {i.start.toFixed(2)}—{i.end.toFixed(2)}秒</p>{i.events.map(e=><p key={e.event_id}>{actionLabel[e.action]||e.action} · {e.status} · {e.times.map(t=>t.toFixed(2)).join(', ')}秒</p>)}<button className="btn btn--secondary" disabled={busy} onClick={async()=>{setBusy(true);try{const job=s.data.jobs.find(j=>j.run_id===i.run_id);if(!job)throw Error('当前工作空间未载入该任务');const scope=await getScope(i.run_id);await s.loadJob(job.job_id);nav('/review?t='+(i.start-scope.start)+'&track='+i.track_id);}catch(e){setError(String(e));}finally{setBusy(false);}}}>查看原视频</button><button className="btn btn--primary" disabled={busy} onClick={async()=>{setBusy(true);setError('');try{const job=s.data.jobs.find(j=>j.run_id===i.run_id);if(!job)throw Error('请先载入对应分析任务');await s.loadJob(job.job_id);const r=await request(`students/${subjectId}/report`,reportSchema,{run_id:i.run_id});s.setReport(r);nav('/report');}catch(e){setError(String(e));}finally{setBusy(false);}}}>生成此片段个人报告</button></div>)}{data?.limitations.map(x=><p className="muted" key={x}>{x}</p>)}</div></section>;
}

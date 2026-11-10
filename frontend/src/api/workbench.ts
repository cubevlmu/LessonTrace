import {z} from 'zod';
import {request} from './client';
import {observationSchema,eventSchema,windowSchema} from '../models/backend';
export async function getRunResults(runId:string){
 const schema=z.object({events:z.array(eventSchema),windows:z.array(windowSchema),truncated:z.boolean(),next_offset:z.number().nullable().optional()});
 const windows:z.infer<typeof windowSchema>[]=[],events=new Map<string,z.infer<typeof eventSchema>>();let offset=0;
 for(let page=0;page<100;page++){
  const r=await request('workbench/results?run_id='+encodeURIComponent(runId)+'&offset='+offset,schema);windows.push(...r.windows);
  for(const event of r.events){const old=events.get(event.event_id);events.set(event.event_id,{...event,display_blind_observations:[...new Map([...(old?.display_blind_observations||[]),...(event.display_blind_observations||[])].map(o=>[JSON.stringify(o),o])).values()]});}
  if(!r.truncated)return {events:[...events.values()],windows,truncated:false};
  if(r.next_offset==null||r.next_offset<=offset)throw Error('保存结果分页不完整，请重试');offset=r.next_offset;
 }
 throw Error('历史窗口超过显示上限，请限定录像范围');
}
export const getFullPlan=(runId:string)=>request('workbench/full-analysis-plan?run_id='+encodeURIComponent(runId),z.object({total_tracks:z.number(),selected_tracks:z.number(),selected_windows:z.number(),maximum_model_requests:z.number(),visible_track_seconds:z.number(),window_seconds:z.number(),rough_processing_seconds:z.number().nullable()}));
const group=z.object({group_id:z.string(),run_id:z.string(),track_ids:z.array(z.number()),reason:z.string(),status:z.enum(['operator_reported','demo_spatial_association']),identity_verified:z.literal(false)});
export type TrackGroup=z.infer<typeof group>;
export const getTrackGroups=(runId:string)=>request('workbench/track-groups?run_id='+encodeURIComponent(runId),z.array(group));
export const saveTrackGroup=(run_id:string,track_ids:number[],reason:string)=>request('workbench/track-groups',group,{run_id,track_ids,reason});
export const revokeTrackGroup=(id:string)=>request('workbench/track-groups/'+encodeURIComponent(id)+'/revoke',z.object({revoked:z.boolean()}),{});
const label=z.object({subject_id:z.string(),display_name:z.string(),track_id:z.number().int(),local_start:z.number(),local_end:z.number(),status:z.enum(['human_confirmed','candidate','track_label']),source_kind:z.string().optional()});
const identities=z.object({bindings:z.array(label),candidates:z.array(label),track_labels:z.array(label).default([]),classroom:z.object({classroom_id:z.string(),name:z.string(),registered_count:z.number(),reference_only_count:z.number()}).nullable(),matching_status:z.string(),global_offset:z.number()});
export type IdentityLabel=z.infer<typeof label>;
export type WorkbenchIdentity=z.infer<typeof identities>;
export const getPlaybackObservations=(runId:string,start:number,end:number)=>request(`workbench/observations?run_id=${encodeURIComponent(runId)}&start=${start}&end=${end}`,z.array(observationSchema));
export const getPlaybackIdentities=(runId:string)=>request('workbench/identities?run_id='+encodeURIComponent(runId),identities).then(value=>identities.parse(value));
export function personLabel(labels:IdentityLabel[],trackId:number,time:number){
 const fallback='人物 '+String(trackId).padStart(3,'0');
 const scoped=labels.filter(b=>b.track_id===trackId&&b.local_start<=time&&time<=b.local_end);
 const confirmed=scoped.filter(b=>b.status==='human_confirmed');
 const registered=scoped.filter(b=>b.status==='track_label');
 const chosen=confirmed.length?confirmed:registered.length?registered:scoped.filter(b=>b.status==='candidate');
 const people=Array.from(new Set(chosen.map(b=>b.subject_id)));
 if(people.length>1)return fallback+' · 关联冲突';
 const name=chosen[0]?.display_name;
 return name?name+(confirmed.length?'':registered.length?(chosen[0].source_kind==='demo_setup_track_label'?' · 演示标记':' · 轨迹登记'):' · AI候选'):fallback;
}
/** Stable demo roster name in a row/selector; video overlays remain time-scoped. */
export function personTrackLabel(labels:IdentityLabel[],trackId:number,time:number){
 const active=labels.filter(l=>l.track_id===trackId&&l.local_start<=time&&time<=l.local_end);
 if(active.length)return personLabel(labels,trackId,time);
 const demo=labels.filter(l=>l.track_id===trackId&&l.status==='track_label'&&l.source_kind==='demo_setup_track_label');
 if(new Set(demo.map(l=>l.subject_id)).size===1)return demo[0].display_name+' · 演示标记';
 return personLabel(labels,trackId,time);
}

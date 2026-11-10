import type {Track,WindowResult} from '../models/backend';
import {observationText,observationView,type VisualItem} from '../models/view';

export interface ActionSpan {id:string;trackId:number;start:number;end:number;title:string;kind:'point'|'estimate'|'human';samples:number[];windowIds:string[];itemId?:string;segmentId:number;maxGap:number}

/** AI spans are candidate state estimates between real samples, never exact boundaries. */
export function actionSpans(windows:WindowResult[],items:VisualItem[],tracks:Track[]):ActionSpan[]{
 const parts:ActionSpan[]=[];
 for(const w of windows){
  if(w.status!=='completed')continue;
  const invisible=(w.summary.observations?.limitations||[]).some(t=>/\btarget\s+is\s+not\s+visible\b|目标(?:人物)?(?:在画面中)?不可见/i.test(t));
  for(const [i,observation] of (w.summary.observations?.observed_actions||[]).entries()){
   const times=w.summary.frames.filter(f=>observation.evidence_refs.includes(f.frame_id)).map(f=>f.local_time).sort((a,b)=>a-b);
   if(!times.length)continue;
   const title=observationText(observation.action).trim();
   const item=items.find(v=>v.trackId===w.track_id&&v.frames.some(f=>observation.evidence_refs.includes(f.frame_id)&&times.some(t=>Math.abs(t-f.local_time)<.000001)));
   parts.push({id:w.window_id+':'+i,trackId:w.track_id,start:times[0],end:invisible?times[0]:times[times.length-1],title,kind:!invisible&&times.length>1?'estimate':'point',samples:times,windowIds:[w.window_id],itemId:item?.id,segmentId:typeof w.segment_id==='number'?w.segment_id:-1,maxGap:invisible?0:Math.min(8,w.end-w.start)});
  }
 }
 parts.sort((a,b)=>a.trackId-b.trackId||a.start-b.start||a.title.localeCompare(b.title));
 const merged:ActionSpan[]=[];
 for(const part of parts){
  const prior=[...merged].reverse().find(p=>p.trackId===part.trackId&&p.title===part.title&&p.segmentId===part.segmentId);
  const contiguous=tracks.find(t=>t.track_id===part.trackId)?.segments.some(s=>s.local_start<=Math.min(prior?.start??part.start,part.start)+.002&&s.local_end>=Math.max(prior?.end??part.end,part.end)-.002);
  if(prior&&part.segmentId>=0&&part.title!=='该帧动作未看清'&&part.start-prior.end<=Math.min(prior.maxGap,part.maxGap)+.002&&contiguous){
   prior.end=Math.max(prior.end,part.end);prior.samples=Array.from(new Set([...prior.samples,...part.samples])).sort((a,b)=>a-b);prior.windowIds=Array.from(new Set([...prior.windowIds,...part.windowIds]));prior.kind=prior.samples.length>1?'estimate':'point';
  }else merged.push({...part});
 }
 for(const item of items){if(item.humanCorrection?.status==='confirmed'){
  const v=observationView(item);merged.push({id:'human:'+item.id,trackId:v.trackId,start:v.localStart,end:v.localEnd,title:v.title,kind:'human',samples:item.frames.map(f=>f.local_time),windowIds:[],itemId:item.id,segmentId:-1,maxGap:0});
 }}
 return merged.sort((a,b)=>a.trackId-b.trackId||a.start-b.start);
}

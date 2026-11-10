import type {Track,WindowResult} from '../models/backend';
export type CoverageState='observed'|'unobservable'|'running'|'pending'|'error'|'unplanned';
export interface CoverageInterval{trackId:number;start:number;end:number;state:CoverageState}
export const coverageNames:Record<CoverageState,string>={observed:'已取证',unobservable:'动作未看清',running:'分析中',pending:'待分析',error:'需重试',unplanned:'尚未分析'};
/** Work coverage is separate from action duration and recognition correctness. */
export function coverageIntervals(tracks:Track[],windows:WindowResult[],offset:number):CoverageInterval[]{
 const out:CoverageInterval[]=[];
 for(const track of tracks)for(const segment of track.segments){
  const relevant=windows.filter(w=>w.track_id===track.track_id&&w.end-offset>=segment.local_start&&w.start-offset<=segment.local_end);
  const points=[...new Set([segment.local_start,segment.local_end,...relevant.flatMap(w=>[Math.max(segment.local_start,w.start-offset),Math.min(segment.local_end,w.end-offset)])])].sort((a,b)=>a-b);
  for(let i=0;i<Math.max(1,points.length-1);i++){
   const start=points[i],end=points[i+1]??start,mid=(start+end)/2;
   const hits=relevant.filter(w=>w.start-offset<=mid+.000001&&w.end-offset>=mid-.000001);
   const done=hits.filter(w=>w.status==='completed');
   const state:CoverageState=done.length?(done.some(w=>w.summary.observations?.observed_actions.length)?'observed':'unobservable'):hits.some(w=>w.status==='running')?'running':hits.some(w=>w.status==='pending')?'pending':hits.some(w=>w.status==='error')?'error':'unplanned';
   const prior=out[out.length-1];if(prior&&prior.trackId===track.track_id&&prior.state===state&&Math.abs(prior.end-start)<.000001)prior.end=end;else out.push({trackId:track.track_id,start,end,state});
  }
 }
 return out;
}

import type {Track,TrackObservation} from '../models/backend';
import type {TrackGroup} from '../api/workbench';
import type {IdentityLabel} from '../api/workbench';
export const canonicalTrack=(id:number,groups:TrackGroup[])=>groups.find(g=>g.track_ids.includes(id))?.track_ids[0]??id;
export const memberTracks=(id:number,groups:TrackGroup[])=>groups.find(g=>g.track_ids.includes(id))?.track_ids??[id];
/** Synthetic classroom aliases may follow a display group, never real identities. */
export function presentationLabels(labels:IdentityLabel[],groups:TrackGroup[]):IdentityLabel[]{
 return labels.flatMap(label=>{
  if(!groups.some(g=>g.track_ids.includes(label.track_id)))return [label];
  if(label.status==='track_label'&&label.source_kind==='demo_setup_track_label')return [{...label,track_id:canonicalTrack(label.track_id,groups)}];
  return [];
 });
}
/** Display grouping only: keep observation gaps and original tracker rows unchanged. */
export function groupedTracks(tracks:Track[],groups:TrackGroup[]):Track[]{
 const result=new Map<number,Track>();
 for(const t of tracks){const id=canonicalTrack(t.track_id,groups),prior=result.get(id);
  if(prior){prior.segments.push(...t.segments);prior.observed_frame_count+=t.observed_frame_count;}
  else result.set(id,{...t,track_id:id,segments:[...t.segments]});
 }
 return [...result.values()].map(t=>({...t,segments:t.segments.sort((a,b)=>a.local_start-b.local_start)}));
}
/** Collapse only overlapping duplicate boxes of an explicit group at the same frame. */
export function groupedObservations(rows:TrackObservation[],groups:TrackGroup[]):TrackObservation[]{
 const kept:TrackObservation[]=[];
 for(const row of [...rows].sort((a,b)=>b.confidence-a.confidence)){
  const id=canonicalTrack(row.track_id,groups);
  const duplicate=kept.some(p=>p.frame_index===row.frame_index&&Math.abs(p.local_timestamp-row.local_timestamp)<.000001&&p.track_id===id&&memberTracks(id,groups).length>1&&
    Math.max(0,Math.min(p.bbox_x2,row.bbox_x2)-Math.max(p.bbox_x1,row.bbox_x1))*Math.max(0,Math.min(p.bbox_y2,row.bbox_y2)-Math.max(p.bbox_y1,row.bbox_y1)) / Math.max(1,Math.min((p.bbox_x2-p.bbox_x1)*(p.bbox_y2-p.bbox_y1),(row.bbox_x2-row.bbox_x1)*(row.bbox_y2-row.bbox_y1)))>.75);
  if(!duplicate)kept.push({...row,track_id:id});
 }
 return kept;
}

import type { TrackObservation } from "../models/backend";
export function projectBox(o: TrackObservation, width: number, height: number) {
  const scale = Math.min(width / o.frame_width, height / o.frame_height);
  return {
    x: (width - o.frame_width * scale) / 2 + o.bbox_x1 * scale,
    y: (height - o.frame_height * scale) / 2 + o.bbox_y1 * scale,
    width: (o.bbox_x2 - o.bbox_x1) * scale,
    height: (o.bbox_y2 - o.bbox_y1) * scale,
  };
}
export function observationAt(rows: TrackObservation[], time: number) {
  return rows.find(
    (o) =>
      o.tracker_state === "matched_visible_detection" &&
      Math.abs(o.local_timestamp - time) <= 0.002,
  );
}

export function observationFrames(rows:TrackObservation[]){
 const frames=new Map<number,TrackObservation[]>();
 for(const row of rows){if(row.tracker_state!=='matched_visible_detection')continue;const items=frames.get(row.local_timestamp)||[];items.push(row);frames.set(row.local_timestamp,items);}
 return {times:Array.from(frames.keys()).sort((a,b)=>a-b),frames};
}
export function visibleObservations(index:ReturnType<typeof observationFrames>,time:number){
 const times=index.times;let lo=0,hi=times.length;
 while(lo<hi){const mid=(lo+hi)>>1;if(times[mid]<=time+.002)lo=mid+1;else hi=mid;}
 const i=lo-1;if(i<0)return [];
 const step=i+1<times.length?times[i+1]-times[i]:i>0?times[i]-times[i-1]:0;
 // Display-hold the last real sampled detection, bounded by the next frame
 // and 200ms. No interpolation or persisted invented observation is created.
 const tolerance=Math.min(.2,Math.max(.002,step+.002));
 if(time-times[i]>tolerance)return [];
 return index.frames.get(times[i])||[];
}

import type { DiscoveredEvent, WindowResult } from "../models/backend";
import { actionLabel, type VisualItem } from "../models/view";
export function visualItems(
  events: DiscoveredEvent[],
  windows: WindowResult[],
  globalOffset?:number,
): VisualItem[] {
  const firstFrame=windows.find(w=>w.summary.frames.length)?.summary.frames[0]||events.find(e=>e.frames.length)?.frames[0];
  const offset=globalOffset??(firstFrame?firstFrame.global_time-firstFrame.local_time:0);
  const items: VisualItem[] = events.map((e): VisualItem => {
    const observations = (e.display_blind_observations || []).filter(o =>
      o.evidence_refs.length > 0 && o.evidence_refs.every(ref => e.frames.some(f => f.frame_id === ref)));
    return {
      id: e.event_id,
      eventId: e.event_id,
      kind: "event",
      trackId: e.track_id,
      localStart: e.local_start,
      localEnd: e.local_end,
      category: e.action_category,
      state:
        e.final_decision === "error"
          ? "error"
          : e.action_category === "unknown"
            ? "unknown"
            : e.final_decision,
      description: observations.map(o => o.action),
      frames: e.frames.map(f => {
        const actions = observations.filter(o => o.evidence_refs.includes(f.frame_id)).map(o => o.action);
        if (!actions.length && e.final_decision === "confirmed" && e.evidence_refs.includes(f.frame_id)
            && e.action_category !== "unknown" && e.action_category !== "other_observable_action"
            && actionLabel[e.action_category]) {
          actions.push(actionLabel[e.action_category]);
        }
        return {
          ...f,
          actions: e.final_decision === "rejected" ? [] : actions,
          url: `/api/context/analysis/frames/${encodeURIComponent(e.event_id)}/${encodeURIComponent(f.frame_id)}`,
        };
      }),
      source: "model_discovered_event",
      limitations: e.limitations,
      raw: e,
      reviewStatus: e.review_status,
      humanCorrection:e.human_correction,
    };
  });
  for (const w of windows) {
    if(w.status!=="completed"){
      items.push({id:'window:'+w.window_id,kind:'window',trackId:w.track_id,localStart:w.start-offset,localEnd:w.end-offset,
        category:'unknown',state:w.status,description:[],frames:[],source:'blind_visual_observation',limitations:w.summary.error?[w.summary.error]:[],raw:w,reviewStatus:'unreviewed'});
      continue;
    }
    const observations=w.summary.observations?.observed_actions||[];
    const sameFrame=(a:{frame_id:string;local_time:number},b:{frame_id:string;local_time:number})=>a.frame_id===b.frame_id&&Math.abs(a.local_time-b.local_time)<.000001;
    const covered=(o:typeof observations[number])=>events.some(e=>
      e.track_id===w.track_id&&(e.display_blind_observations||[]).some(shown=>shown.action===o.action&&o.evidence_refs.every(ref=>shown.evidence_refs.includes(ref)))&&o.evidence_refs.every(ref=>
        w.summary.frames.some(wf=>wf.frame_id===ref&&e.frames.some(f=>sameFrame(f,wf)))));
    const uncovered=observations.filter(o=>o.evidence_refs.length>0&&!covered(o));
    const hasEventFrame=events.some(e=>e.track_id===w.track_id&&e.frames.some(f=>w.summary.frames.some(wf=>sameFrame(wf,f))));
    if(!uncovered.length&&(observations.length||hasEventFrame))continue;
    for(const [index,observation] of (uncovered.length?uncovered:[null]).entries()){
    const frames=observation?w.summary.frames.filter(f=>observation.evidence_refs.includes(f.frame_id)):w.summary.frames;
    if(observation&&!frames.length)continue;
    const times=frames.map(f=>f.local_time);
    items.push({
      id: "window:" + w.window_id+(index?':'+index:''),
      kind: "window",
      trackId: w.track_id,
      localStart: times.length?Math.min(...times):w.start,
      localEnd: times.length?Math.max(...times):w.end,
      category: "unknown",
      state: "unknown",
      description: observation?[observation.action]:[],
      frames: frames.map((f) => ({
        ...f,
        actions: observation ? [observation.action] : [],
        url: `/api/context/analysis/windows/${w.window_id}/frames/${f.frame_id}`,
      })),
      source: "blind_visual_observation",
      limitations: w.summary.observations?.limitations || [],
      raw: w,
      reviewStatus: "unreviewed",
    });
    }
  }
  for (const item of items) {
    item.observationCount = new Set(item.frames.filter(f => f.actions?.length).map(f => f.local_time)).size;
  }
  return items.sort(
    (a, b) => a.localStart - b.localStart || a.trackId - b.trackId,
  );
}

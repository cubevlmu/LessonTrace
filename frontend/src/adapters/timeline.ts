import type { Track, WindowResult } from "../models/backend";
import { observationText, observationView, type VisualItem } from "../models/view";

const epsilon = 0.002;

export interface ActionSpan {
  id: string;
  trackId: number;
  start: number;
  end: number;
  title: string;
  kind: "point" | "estimate" | "human";
  samples: number[];
  itemIds: string[];
}

function actionType(text: string) {
  const title = observationText(text).trim().replace(/^(目标人物|该人物)/, "").replace(/[。；]$/, "");
  return /^举[左右]?手$/.test(title) || /^举起[左右]手$/.test(title) ? "举手" : title;
}

interface Sample {
  trackId: number;
  time: number;
  title: string;
  origin: string;
  type: string;
  segment?: number;
  canMerge: boolean;
  itemIds: string[];
}

/** Group nearby observations on the original track before applying display aliases. */
export function actionSpans(windows: WindowResult[], items: VisualItem[], tracks: Track[]): ActionSpan[] {
  const samples: Sample[] = [];
  const evidence = new Map<string, string[]>();
  const frameKey = (trackId: number, time: number, frame: string, action: string) =>
    JSON.stringify([trackId, time, frame, action]);
  for (const item of items) {
    for (const frame of item.frames) {
      for (const action of frame.actions ?? []) {
        const key = frameKey(item.trackId, frame.local_time, frame.frame_id, action);
        const ids = evidence.get(key) ?? [];
        if (!ids.includes(item.id)) ids.push(item.id);
        evidence.set(key, ids);
      }
    }
  }
  const windowFrames = new Set<string>();
  for (const w of windows) {
    if (w.status !== "completed") continue;
    const invisible = w.summary.observations?.limitations?.some(t =>
      /\btarget\s+is\s+not\s+visible\b|目标(?:人物)?(?:在画面中)?不可见/i.test(t));
    for (const [i, observation] of (w.summary.observations?.observed_actions ?? []).entries()) {
      const frames = w.summary.frames.filter(f => observation.evidence_refs.includes(f.frame_id))
        .sort((a, b) => a.local_time - b.local_time);
      for (const f of frames) windowFrames.add(frameKey(w.track_id, f.local_time, f.frame_id, observation.action));
      const title = observationText(observation.action).trim();
      if (!title) continue;
      for (const f of invisible ? frames.slice(0, 1) : frames) {
        samples.push({
          trackId: w.track_id, time: f.local_time, title, type: actionType(title), origin: w.window_id + ":" + i,
          segment: typeof w.segment_id === "number" ? w.segment_id : undefined,
          canMerge: !invisible && title !== "该帧动作未看清",
          itemIds: evidence.get(frameKey(w.track_id, f.local_time, f.frame_id, observation.action)) ?? [],
        });
      }
    }
  }
  // Older saved events can have cited frames even when their source windows are unavailable.
  for (const item of items) {
    if (item.kind !== "event" || item.humanCorrection || item.state === "error" || item.state === "rejected") continue;
    for (const f of item.frames) {
      for (const action of f.actions ?? []) {
        const key = frameKey(item.trackId, f.local_time, f.frame_id, action);
        if (windowFrames.has(key)) continue;
        const title = observationText(action).trim();
        if (title) samples.push({ trackId: item.trackId, time: f.local_time, title, type: actionType(title),
          origin: JSON.stringify([item.id, action]), canMerge: true, itemIds: [item.id] });
      }
    }
  }
  samples.sort((a, b) => a.trackId - b.trackId || a.time - b.time);

  const spans: ActionSpan[] = [];
  const spanMeta = new Map<ActionSpan, { canMerge: boolean }>();
  const trackSegments = new Map(tracks.map(t => [t.track_id, t.segments]));
  const sampleTimesByTrack = new Map<number, number[]>();
  type Chain = { span: ActionSpan; sample: Sample };
  let previous = new Map<string, Chain>();
  let current = new Map<string, Chain>();
  let lastTrack: number | undefined, lastTime: number | undefined;
  for (const s of samples) {
    const times = sampleTimesByTrack.get(s.trackId) ?? [];
    if (times[times.length - 1] !== s.time) times.push(s.time);
    sampleTimesByTrack.set(s.trackId, times);

    if (s.trackId !== lastTrack || s.time !== lastTime) {
      // Only actions present at the preceding evidence time can continue.
      previous = s.trackId === lastTrack ? current : new Map();
      current = new Map();
      lastTrack = s.trackId;
      lastTime = s.time;
    }
    const key = JSON.stringify([s.segment, s.type, s.canMerge]);
    let chain = current.get(key);
    if (!chain) {
      const prior = previous.get(key);
      const segments = trackSegments.get(s.trackId);
      const continuous = prior && (segments?.length
        ? segments.some(seg => seg.local_start <= prior.span.end + epsilon && seg.local_end >= s.time - epsilon)
        : s.segment === prior.sample.segment);
      if (prior && s.canMerge && continuous) {
        chain = prior;
      } else {
        const span: ActionSpan = {
          id: s.origin + ":" + s.time, trackId: s.trackId, title: s.title,
          start: s.time, end: s.time, kind: "point", samples: [], itemIds: [],
        };
        spans.push(span);
        spanMeta.set(span, { canMerge: s.canMerge });
        chain = { span, sample: s };
      }
      current.set(key, chain);
    }
    const span = chain.span;
    chain.sample = s;
    if (span.title !== s.title) span.title = s.type;
    if (span.samples[span.samples.length - 1] !== s.time) span.samples.push(s.time);
    span.end = s.time;
    if (span.end > span.start) span.kind = "estimate";
    for (const id of s.itemIds) if (!span.itemIds.includes(id)) span.itemIds.push(id);
  }

  // Extend each discrete state linearly until the next state change within the same visible segment.
  for (const span of spans) {
    if (!spanMeta.get(span)?.canMerge) continue;
    const segments = trackSegments.get(span.trackId) ?? [];
    const seg = segments.find(s => s.local_start <= span.start + epsilon && s.local_end >= span.end - epsilon);
    const times = sampleTimesByTrack.get(span.trackId) ?? [];
    const nextChange = times.find(t =>
      t > span.end + epsilon && (!seg || t <= seg.local_end + epsilon));
    const extendedEnd = nextChange ?? seg?.local_end;
    if (extendedEnd !== undefined && extendedEnd > span.end + epsilon) {
      span.end = extendedEnd;
      span.kind = "estimate";
    }
  }

  for (const item of items) {
    if (item.humanCorrection?.status !== "confirmed") continue;
    const v = observationView(item);
    spans.push({ id: "human:" + item.id, trackId: v.trackId, start: v.localStart, end: v.localEnd,
      title: v.title, kind: "human", samples: item.frames.map(f => f.local_time), itemIds: [item.id] });
  }
  return spans.sort((a, b) => a.trackId - b.trackId || a.start - b.start);
}

export interface ObservationRow {
  item: VisualItem;
  members: VisualItem[];
  start: number;
  end: number;
  title: string;
  samples: number;
}

/** Keep complex, reviewed or differently sourced records individually reachable. */
export function observationRows(items: VisualItem[], spans: ActionSpan[]): ObservationRow[] {
  const memberships = new Map<string, ActionSpan[]>();
  for (const span of spans) {
    for (const id of span.itemIds) {
      const matches = memberships.get(id) ?? [];
      matches.push(span);
      memberships.set(id, matches);
    }
  }
  const rows: ObservationRow[] = [];
  const groups = new Map<string, ObservationRow>();
  for (const item of items) {
    const view = observationView(item);
    const matches = memberships.get(item.id);
    const span = matches?.length === 1 ? matches[0] : undefined;
    const groupable = span && !item.humanCorrection && item.reviewStatus === "unreviewed" &&
      item.state !== "rejected" && item.state !== "error" && actionType(view.title) === actionType(span.title);
    const key = groupable ? JSON.stringify([span.id, view.source]) : undefined;
    const prior = key && groups.get(key);
    if (prior) {
      prior.members.push(item);
      prior.start = Math.min(prior.start, view.localStart);
      prior.end = Math.max(prior.end, view.localEnd);
      prior.title = span!.title;
    } else {
      const row = { item, members: [item], start: view.localStart, end: view.localEnd, title: view.title, samples: 0 };
      rows.push(row);
      if (key) groups.set(key, row);
    }
  }
  for (const row of rows) {
    row.samples = new Set(row.members.flatMap(i => i.frames.filter(f => f.actions?.length).map(f => f.local_time))).size;
  }
  return rows.sort((a, b) => a.start - b.start || a.item.trackId - b.item.trackId);
}

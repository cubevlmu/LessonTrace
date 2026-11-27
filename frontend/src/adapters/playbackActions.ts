import type { Frame, Track, WindowResult } from "../models/backend";
import { observationText, observationView, type VisualItem } from "../models/view";

interface ActionSample {
  start: number;
  end: number;
  titles: Set<string>;
}

interface TrackActions {
  samples: ActionSample[];
  corrections: { start: number; end: number; title: string }[];
}

export type PlaybackActions = Map<number, TrackActions>;

// Allow rounded seek times such as 40.27 for a sample at 40.2667.
const timeEpsilon = 0.005;

export function playbackActions(
  items: VisualItem[],
  windows: WindowResult[],
  clipStart?: number,
  tracksData?: Track[],
): PlaybackActions {
  const tracks: PlaybackActions = new Map();
  const samplesByTrack = new Map<number, Map<number, ActionSample>>();
  const maxEndByTrack = new Map<number, number>();
  const trackSegments = new Map((tracksData ?? []).map(t => [t.track_id, t.segments]));
  function track(id: number) {
    let entry = tracks.get(id);
    if (!entry) {
      entry = { samples: [], corrections: [] };
      tracks.set(id, entry);
    }
    return entry;
  }

  function recordFrames(id: number, frames: (Frame & { actions?: string[] })[], end: number) {
    maxEndByTrack.set(id, Math.max(maxEndByTrack.get(id) ?? 0, end));
    let samples = samplesByTrack.get(id);
    if (!samples) {
      samples = new Map();
      samplesByTrack.set(id, samples);
    }
    const ordered = [...frames].sort((a, b) => a.local_time - b.local_time);
    for (const frame of ordered) {
      const titles = (frame.actions ?? [])
        .map(action => observationText(action).trim())
        .filter(Boolean);
      if (!titles.length) continue;
      const start = frame.local_time;
      const sample = samples.get(start) ?? { start, end: start, titles: new Set<string>() };
      for (const title of titles) sample.titles.add(title);
      samples.set(start, sample);
    }
  }

  for (const w of windows) {
    const first = w.summary.frames[0];
    if (w.status === "completed" && first) {
      recordFrames(w.track_id, w.summary.frames, w.end - (first.global_time - first.local_time));
    }
  }
  for (const item of items) {
    const correction = item.humanCorrection;
    if (correction?.status === "confirmed") {
      const first = item.frames[0];
      const offset = clipStart ?? (first && first.global_time - first.local_time);
      if (offset !== undefined) {
        track(correction.track_id).corrections.push({
          start: correction.start - offset,
          end: correction.end - offset,
          title: observationText(correction.observable_action.trim()) || observationView(item).title,
        });
      }
    } else if (correction?.status !== "rejected" && item.state !== "rejected" && item.state !== "error") {
      recordFrames(item.trackId, item.frames, item.localEnd);
    }
  }
  for (const [id, samples] of samplesByTrack) {
    const ordered = [...samples.values()].sort((a, b) => a.start - b.start);
    const segments = trackSegments.get(id) ?? [];
    const fallbackEnd = maxEndByTrack.get(id) ?? 0;
    for (const [i, sample] of ordered.entries()) {
      const seg = segments.find(s => s.local_start <= sample.start + timeEpsilon && s.local_end >= sample.start - timeEpsilon);
      const next = ordered[i + 1];
      const nextInSameSegment = next && (!seg || next.start <= seg.local_end + timeEpsilon);
      const extendedEnd = nextInSameSegment
        ? next.start
        : (seg ? seg.local_end : Math.max(sample.start, fallbackEnd));
      sample.end = Math.max(sample.start, extendedEnd);
    }
    track(id).samples = ordered;
  }
  return tracks;
}

export function playbackActionAt(index: PlaybackActions | undefined, id: number, time: number): string {
  const track = index?.get(id);
  if (track) {
    const confirmed = track.corrections.filter(c => c.start <= time + timeEpsilon && c.end >= time - timeEpsilon);
    if (confirmed.length) return [...new Set(confirmed.map(c => c.title))].join("、");

    let low = 0, high = track.samples.length;
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (track.samples[mid].start <= time + timeEpsilon) low = mid + 1;
      else high = mid;
    }
    const sample = track.samples[low - 1];
    if (sample?.titles.size && time <= sample.end + timeEpsilon) return [...sample.titles].join("、");
  }
  return "暂无动作记录";
}

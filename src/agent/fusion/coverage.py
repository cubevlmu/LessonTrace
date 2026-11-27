"""Coverage of actual discovery attempts, independent of selected evidence."""
import json
import math

from pydantic import BaseModel, Field

from src.attribution.schemas import ActorObservations


class Coverage(BaseModel):
    observed_windows: int = Field(ge=0, strict=True)
    failed_windows: int = Field(ge=0, strict=True)


def valid_observation_result(payload):
    """Read a persisted blind observation, including successful empty responses."""
    try:
        result = json.loads(payload)['result']
        observations = ActorObservations.model_validate(result['observations'], strict=True)
        frames = result['frames']
        if not isinstance(frames, list) or not frames:
            return None
        ids = set()
        for frame in frames:
            fid = frame['frame_id']
            if not isinstance(fid, str) or not fid or fid in ids:
                return None
            if not all(type(t) in (int, float) and math.isfinite(t) for t in
                       (frame['local_time'], frame['global_time'])):
                return None
            ids.add(fid)
        if any(not set(action.evidence_refs) <= ids for action in observations.observed_actions):
            return None
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def window_coverage(service, plan, actor):
    """Count distinct owner-visible window records in the effective global range."""
    observed = failed = 0
    with service.repo.connection() as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_windows'"
        ).fetchone():
            return None
        if plan.effective_time_range is None:
            return Coverage(observed_windows=0, failed_windows=0)
        lo, hi = plan.effective_time_range
        intervals = {}
        if plan.roster_subject_id:
            for interval in plan.profile_intervals:
                a, b = max(lo, interval['start']), min(hi, interval['end'])
                if a <= b:
                    intervals.setdefault(interval['track_id'], []).append((a, b))
            if not intervals:
                return Coverage(observed_windows=0, failed_windows=0)
        rows = connection.execute(
            """SELECT w.window_id, w.track_id, w.start, w.end, w.status, w.payload
               FROM discovery_windows w
               JOIN analysis_jobs j ON j.job_id=w.job_id AND j.run_id=w.run_id
               JOIN analysis_videos v ON v.video_id=j.video_id AND v.owner=j.owner
               WHERE j.owner=? AND v.status='active' AND w.run_id=? AND v.clip_id=?
                 AND w.start<=? AND w.end>=? AND w.start<=w.end
                 AND (? IS NULL OR w.track_id=?)
                 AND w.status IN ('completed', 'error')""",
            (actor, plan.run_id, plan.clip_id, hi, lo, plan.track_id, plan.track_id),
        )
        seen = set()
        for row in rows:
            if row['window_id'] in seen:
                continue
            if plan.roster_subject_id and not any(
                row['start'] <= b and a <= row['end'] for a, b in intervals.get(row['track_id'], ())
            ):
                continue
            seen.add(row['window_id'])
            if row['status'] == 'completed' and valid_observation_result(row['payload']) is not None:
                observed += 1
            else:
                failed += 1
    return Coverage(observed_windows=observed, failed_windows=failed)

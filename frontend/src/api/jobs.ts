import { z } from "zod";
import {
  jobSchema,
  eventSchema,
  windowSchema,
  trackSchema,
  observationSchema,
  scopeSchema,
} from "../models/backend";
import { request } from "./client";
export const getRuntime = () =>
  request(
    "analysis/runtime",
    z.object({
      status: z.enum(["available", "unavailable"]),
      model_id: z.string(),
      model_digest: z.string().nullable(),
      message: z.string(),
    }),
  );
export const getDiagnostics = (id: string) =>
  request(
    "analysis/jobs/" + id + "/diagnostics",
    z.object({
      errors: z.array(
        z.object({
          operation: z.string(),
          created_at: z.string(),
          message: z.string(),
          detail: z.string(),
        }),
      ),
    }),
  );
export const listJobs = () => request("analysis/jobs", z.array(jobSchema));
export const getJob = (id: string) => request("analysis/jobs/" + id, jobSchema);
export const startJob = (videoId: string, config: Record<string, unknown>,reuseTrackingRunId?:string) =>
  request("analysis/jobs", z.object({ job: jobSchema, created: z.boolean() }), {
    video_id: videoId,
    config,
    ...(reuseTrackingRunId?{reuse_tracking_run_id:reuseTrackingRunId}:{}),
  });
export const controlJob = (
  id: string,
  action: "pause" | "resume" | "cancel" | "retry",
  additionalCalls = 0,
) =>
  request("analysis/jobs/" + id + "/control", jobSchema, {
    action,
    additional_calls: additionalCalls,
  });
export const getEvents = (id: string) =>
  request("analysis/jobs/" + id + "/events", z.array(eventSchema));
export const getWindows = (id: string) =>
  request("analysis/jobs/" + id + "/windows", z.array(windowSchema));
export const getTracks = (run: string) =>
  request("tracks?run_id=" + run, z.array(trackSchema));
export const getObservations = (run: string, track: number) =>
  request(
    `fusion/observations?run_id=${run}&track_id=${track}`,
    z.array(observationSchema),
  );
export const getScope = (run: string) =>
  request("fusion/scope?run_id=" + run, scopeSchema);
export const presets = {
  server: {max_tracks:1,max_windows:1,max_calls:2,budget_seconds:360,timeout:240,frames:2,input_long_edge:320,stride:4,device:'cpu'},
  quick: { max_tracks: 2, max_windows: 2, max_calls: 4, budget_seconds: 450 },
  standard: {
    max_tracks: 3,
    max_windows: 6,
    max_calls: 12,
    budget_seconds: 600,
  },
};

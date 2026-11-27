import { z } from "zod";

const finite = z.number().finite();
const range = z
  .tuple([finite, finite])
  .refine(([a, b]) => a <= b, "时间范围倒置");
export const accountSchema = z.object({
  actor: z.string(),
  username: z.string().optional(),
  role: z.enum(["admin", "teacher", "reviewer", "viewer"]),
  must_change_password: z.boolean().optional(),
});
export type Account = z.infer<typeof accountSchema>;
export const videoSchema = z
  .object({
    video_id: z.string(),
    status: z.string(),
    created_at: z.string(),
    metadata: z
      .object({
        filename: z.string().optional(),
        duration: finite.positive(),
        width: finite.positive(),
        height: finite.positive(),
      })
      .passthrough(),
  })
  .passthrough();
export type Video = z.infer<typeof videoSchema>;
export const jobSchema = z
  .object({
    job_id: z.string(),
    video_id: z.string(),
    run_id: z.string().nullable(),
    status: z.enum([
      "queued",
      "running",
      "pausing",
      "paused",
      "cancelling",
      "cancelled",
      "failed",
      "completed",
      "completed_with_errors",
    ]),
    stage: z.string(),
    processed_windows: z.number().int().nonnegative(),
    total_windows: z.number().int().nonnegative(),
    failed_windows: z.number().int().nonnegative(),
    attempted_calls: z.number().int().nonnegative(),
    completed_calls: z.number().int().nonnegative(),
    failed_calls: z.number().int().nonnegative(),
    call_limit: z.number().int().nonnegative(),
    config_and_runtime: z
      .object({
        elapsed_seconds: finite.optional(),
        config: z.record(z.unknown()),
        coverage: z
          .object({
            selected_tracks: z.number(),
            total_tracks: z.number(),
            selected_windows: z.number(),
            eligible_windows_before_budget: z.number(),
            omitted_windows: z.number(),
            time_bins: z.number().int().positive().optional(),
            empty_time_bins: z.array(z.number().int().positive()).optional(),
          })
          .passthrough()
          .optional(),
      })
      .passthrough(),
  })
  .passthrough();
export type Job = z.infer<typeof jobSchema>;
export const frameSchema = z
  .object({
    frame_id: z.string(),
    frame_index: z.number().int().nonnegative(),
    local_time: finite.nonnegative(),
    global_time: finite.nonnegative(),
    width: finite.positive(),
    height: finite.positive(),
    roi_xyxy: z.array(finite).length(4),
  })
  .passthrough();
export type Frame = z.infer<typeof frameSchema>;
const observation = z.object({
  action: z.string(),
  evidence_refs: z.array(z.string()),
});
export const eventSchema = z
  .object({
    event_id: z.string(),
    track_id: z.number().int().positive(),
    run_id: z.string(),
    action_category: z.string(),
    local_start: finite,
    local_end: finite,
    observed_time_support: z.array(finite),
    final_decision: z.enum(["confirmed", "rejected", "uncertain", "error"]),
    raw_vlm_decisions: z.array(z.string()),
    reason: z.string(),
    review_status: z.string(),
    frames: z.array(frameSchema),
    evidence_refs: z.array(z.string()),
    limitations: z.array(z.string()),
    subject_checks: z
      .object({ passed: z.boolean(), failures: z.array(z.string()) })
      .passthrough(),
    display_blind_observations: z.array(observation).optional(),
    human_correction:z.object({status:z.string(),reviewer:z.string(),review_time:z.string(),corrected_category:z.string(),track_id:z.number(),start:finite,end:finite,observable_action:z.string()}).nullable().optional(),
  })
  .passthrough();
export type DiscoveredEvent = z.infer<typeof eventSchema>;
export const windowSchema = z
  .object({
    window_id: z.string(),
    track_id: z.number().int().positive(),
    run_id: z.string(),
    start: finite,
    end: finite,
    status: z.string(),
    summary: z.object({
      observations: z
        .object({
          observed_actions: z.array(observation),
          limitations: z.array(z.string()).optional(),
        })
        .nullable()
        .optional(),
      unknown_or_empty: z.boolean().nullable().optional(),
      error: z.string().nullable().optional(),
      frames: z.array(frameSchema),
    }),
  })
  .passthrough();
export type WindowResult = z.infer<typeof windowSchema>;
export const coverageSchema = z.object({
  observed_windows: z.number().int().nonnegative(),
  failed_windows: z.number().int().nonnegative(),
});
export type Coverage = z.infer<typeof coverageSchema>;
export const scopeSchema = z.object({
  run_id: z.string().optional(),
  clip_id: z.string(),
  video_id: z.string(),
  start: finite,
  end: finite,
  width: finite.positive(),
  height: finite.positive(),
  coverage: coverageSchema.nullable().optional(),
});
export type Scope = z.infer<typeof scopeSchema>;
export const trackSchema = z
  .object({
    run_id: z.string(),
    track_id: z.number().int().positive(),
    observed_frame_count: z.number().int(),
    segments: z.array(
      z
        .object({
          global_start: finite,
          global_end: finite,
          local_start: finite,
          local_end: finite,
        })
        .passthrough(),
    ),
  })
  .passthrough();
export type Track = z.infer<typeof trackSchema>;
export const observationSchema = z
  .object({
    frame_index: z.number().int(),
    track_id: z.number().int(),
    local_timestamp: finite,
    global_timestamp: finite,
    bbox_x1: finite,
    bbox_y1: finite,
    bbox_x2: finite,
    bbox_y2: finite,
    frame_width: finite.positive(),
    frame_height: finite.positive(),
    confidence: finite,
    tracker_state: z.string(),
  })
  .passthrough();
export type TrackObservation = z.infer<typeof observationSchema>;
const state = z.enum(["supported", "uncertain", "contradicted", "unknown"]);
export const claimSchema = z
  .object({
    fact_id: z.string(),
    statement: z.string(),
    scope: z.string(),
    evidence_refs: z.array(z.string()).min(1),
    final_claim_status: state,
    subject_ref: z.string().nullable(),
    time_scope: range.nullable(),
  })
  .passthrough();
export type Claim = z.infer<typeof claimSchema>;
export const evidenceSchema = z.object({
  evidence_id: z.string(),
  evidence_type: z.string(),
  source_id: z.string(),
  content: z.string(),
  verification_status: state,
  subject_ref: z.string().nullable(),
  time_scope: range.nullable(),
  provenance: z.record(z.unknown()),
  source_version: z.string(),
});
export type Evidence = z.infer<typeof evidenceSchema>;
const risk = z
  .object({
    kind: z.string(),
    reason: z.string(),
    evidence_refs: z.array(z.string()),
  })
  .passthrough();
const v2ReportFields = {
  statements: z.array(z.object({
    statement_id: z.string(),
    role: z.string(),
    section_id: z.string().optional(),
    text: z.string(),
    evidence_refs: z.array(z.string()).default([]),
    depends_on_statements: z.array(z.string()).default([]),
    relation_refs: z.array(z.string()).default([]),
    query_refs: z.array(z.string()).default([]),
    change_event_refs: z.array(z.string()).default([]),
  }).passthrough()).optional(),
  clarification_candidates: z.array(z.object({
    subject_id: z.string(),
    display_name: z.string(),
    classroom_name: z.string().optional(),
    has_submission: z.boolean().optional(),
  }).passthrough()).optional(),
  scope_key: z.string().optional(),
};
export const reportSchema = z.discriminatedUnion("status", [
  z
    .object({
      ...v2ReportFields,
      report_id: z.string(),
      product_scope:z.object({classroom_name:z.string(),subject_name:z.string().nullable()}).passthrough().nullable().optional(),
      status: z.enum(["current","historical_snapshot"]),
      answer: z.string(),
      coverage: coverageSchema.nullable().optional(),
      query_plan: z.object({
        intent: z.string().optional(),
        effective_time_range: range.nullable().optional(),
        track_id: z.number().int().positive().nullable().optional(),
        roster_subject_id: z.string().nullable().optional(),
        document_ids: z.array(z.string()).optional(),
        material_mode: z.enum(["linked", "selected", "none"]).optional(),
      }).passthrough().optional(),
      direct_answer: z.object({text: z.string().optional(), evidence_refs: z.array(z.string())}).passthrough().optional(),
      boundary_text: z.string().optional(),
      check_text: z.string().optional(),
      check_evidence_refs: z.array(z.string()).optional(),
      narrative:z.array(z.object({heading:z.string(),text:z.string(),evidence_refs:z.array(z.string()),sentence_ids:z.array(z.string()).optional(),analysis_section:z.string().optional()})).optional(),
      narrative_generation:z.object({status:z.string()}).passthrough().optional(),
      recognition_records:z.array(z.object({recognition_state:z.string(),text:z.string(),time_label:z.string(),evidence_refs:z.array(z.string()),source_label:z.string(),reliability:z.enum(['高','中','低']),reliability_basis:z.string(),verification_status:state,subject_ref:z.string().nullable()})).optional(),
      claims: z.array(claimSchema),
      additional_claims: z.array(claimSchema).optional(),
      evidence: z.array(evidenceSchema),
      review_queue: z.array(risk),
      conflicts: z.array(risk),
      classroom: z
        .object({
          run_id: z.string().nullish(),
          clip_id: z.string().nullish(),
          lesson_id: z.string().nullish(),
          time_range: range.nullable(),
        })
        .passthrough(),
      limitations: z.array(z.string()),
      created_at: z.string(),
      trace: z.record(z.unknown()),
      sections: z
        .array(z.object({ heading: z.string(), claims: z.array(claimSchema) }))
        .optional(),
    })
    .passthrough(),
  z
    .object({
      ...v2ReportFields,
      report_id: z.string(),
      status: z.literal("stale"),
      reason: z.string(),
      claims: z.array(z.unknown()),
      answer: z.null(),
      invalid_dependencies: z.array(z.unknown()).optional(),
    })
    .passthrough(),
]);
export type Report = z.infer<typeof reportSchema>;
export const playbackSchema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("available"),
    run_id: z.string(),
    clip_id: z.string(),
    media_url: z.string(),
    duration: finite.positive(),
    local_range: range,
    global_range: range,
    track_id: z.number().int().positive().nullable(),
  }),
  z.object({
    status: z.literal("unavailable"),
    reason: z.enum(["report_stale", "reference_missing", "not_video",
      "source_mismatch", "media_unavailable", "time_out_of_range"]),
  }),
]);
export type Playback = z.infer<typeof playbackSchema>;
export const historySchema = z.array(
  z
    .object({
      review_id: z.string(),
      actor: z.string(),
      created_at: z.string(),
      status: z.string(),
      payload: z.string(),
    })
    .passthrough(),
);
export type ReviewRecord = z.infer<typeof historySchema>[number];

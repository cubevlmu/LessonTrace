import { z } from "zod";
import { request } from "./client";

const readinessStatus = z.enum(["ready", "processing", "needs_confirmation"]);
const endpoint = z.enum(["document", "passage", "item", "student", "video"]);

export const lessonRelationSchema = z.object({
  relation_id: z.string(),
  rev: z.number().int().positive(),
  lesson_id: z.string(),
  src_type: endpoint,
  src_id: z.string(),
  rel_type: z.string(),
  dst_type: endpoint,
  dst_id: z.string(),
  status: z.enum(["candidate", "active", "revoked"]),
  basis_ref: z.string(),
  basis_version: z.string(),
  provenance: z.string(),
  actor: z.string(),
  created_at: z.string(),
}).passthrough();

export const lessonIndexSchema = z.object({
  lesson_id: z.string(),
  title: z.string(),
  classroom_id: z.string().nullable(),
  video_id: z.string().nullable(),
  student_id: z.string().nullable(),
  documents: z.array(z.object({
    document_id: z.string(),
    title: z.string(),
    source_type: z.string(),
    file_type: z.string(),
    status: z.string(),
    version: z.number().int().positive(),
    readiness: readinessStatus,
  }).passthrough()),
  items: z.array(z.object({
    item_id: z.string(),
    lesson_id: z.string(),
    kind: z.enum(["goal", "question", "submission", "annotation", "rubric_clause"]),
    passage_id: z.string(),
    document_id: z.string(),
    local_key: z.string(),
    locator: z.object({
      start: z.number().int().nonnegative(),
      end: z.number().int().positive(),
      page_or_slide: z.number().int().nullable(),
      source_start: z.number().int().nonnegative(),
      source_end: z.number().int().positive(),
    }).passthrough(),
    source_version: z.string(),
    owner: z.string(),
    created_at: z.string(),
  }).passthrough()),
  relations: z.array(lessonRelationSchema),
  candidates: z.array(lessonRelationSchema),
  readiness: z.object({
    status: readinessStatus,
    counts: z.object({
      ready: z.number().int().nonnegative(),
      processing: z.number().int().nonnegative(),
      needs_confirmation: z.number().int().nonnegative(),
    }),
    by_document: z.record(readinessStatus),
  }),
}).passthrough();

export type LessonIndex = z.infer<typeof lessonIndexSchema>;
export type LessonRelation = z.infer<typeof lessonRelationSchema>;

const lessonPath = (id: string) => "lessons/" + encodeURIComponent(id);
const relationPath = (lessonId: string, relationId: string) =>
  lessonPath(lessonId) + "/relations/" + encodeURIComponent(relationId);

export const getLessonIndex = (lessonId: string, studentId?: string) =>
  request(lessonPath(lessonId) + "/index" +
    (studentId ? "?student_id=" + encodeURIComponent(studentId) : ""), lessonIndexSchema);

export const confirmLessonRelation = (lessonId: string, relationId: string, expectedRev?: number) =>
  request(relationPath(lessonId, relationId) + "/confirm", lessonRelationSchema,
    { expected_rev: expectedRev });

export const revokeLessonRelation = (lessonId: string, relationId: string, reason?: string, expectedRev?: number) =>
  request(relationPath(lessonId, relationId) + "/revoke", lessonRelationSchema,
    { reason, expected_rev: expectedRev });

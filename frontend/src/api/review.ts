import { z } from "zod";
import { reportSchema, historySchema } from "../models/backend";
import { request, responseError } from "./client";
export const askReview = (body: Record<string, unknown>) =>
  request("fusion/ask", reportSchema, body);
export const generateReport = (job: string, documentIds:string[]=[]) =>
  request("analysis/jobs/" + job + "/report", reportSchema, {document_ids:documentIds}, "POST");
export const getReport = (id: string) =>
  request("fusion/reports/" + id, reportSchema);
export const refreshReport = (id: string) =>
  request("fusion/reports/" + id + "/refresh", reportSchema, {}, "POST");
export const getHistory = (event: string) =>
  request(
    "validation/attribution/history?candidate_id=" +
      encodeURIComponent("discovered:" + event),
    historySchema,
  );
export const saveReview = (body: Record<string, unknown>) =>
  request(
    "validation/attribution/reviews",
    z
      .object({
        review_id: z.string(),
        decision: z.string(),
        discovered_review_id: z.string().optional(),
      })
      .passthrough(),
    body,
  );
export const revokeReview = (id: string, reason: string) =>
  request(
    "validation/attribution/bindings/discovered:" + id + "/revoke",
    z.object({ status: z.string() }).passthrough(),
    { reason },
  );
export async function exportReport(id: string, format: "json" | "markdown") {
  const response = await fetch(
    `/api/context/fusion/reports/${id}/export/${format}`,
    { credentials: "same-origin" },
  );
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("lessontrace:unauthorized"));
    throw responseError(response.status, await response.text());
  }
  const data = await response.blob();
  const a = document.createElement("a");
  const url = URL.createObjectURL(data);
  a.href = url;
  a.download = `LessonTrace-${id}.${format === "json" ? "json" : "md"}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

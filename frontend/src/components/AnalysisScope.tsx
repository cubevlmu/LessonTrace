import { useEffect, useRef, useState } from "react";
import { useSession } from "../state/session";
import { listMaterials, materialNames, type Material } from "../api/materials";
import { getPlaybackIdentities } from "../api/workbench";
import { getClassroom, type Student } from "../api/classrooms";
import { getScope } from "../api/jobs";
import type { Coverage } from "../models/backend";
import {
  AlertBanner,
  fieldClass,
  labelClass,
  selectClass,
} from "./ui/primitives";

export function formatCoverage(coverage?: Coverage | null) {
  return coverage
    ? `已完成观察 ${coverage.observed_windows} 个分析窗口，失败 ${coverage.failed_windows} 个`
    : "覆盖数未记录";
}

function rangeFilters(target: string, start: string, end: string, offset: number) {
  const a = start.trim() ? Number(start) + offset : null;
  const b = end.trim() ? Number(end) + offset : null;
  if ((a !== null && (!Number.isFinite(a) || a < offset)) ||
      (b !== null && (!Number.isFinite(b) || b < offset)) ||
      (a !== null && b !== null && a > b))
    throw Error("请输入合法且有序的视频内时间范围");
  return {
    track_id: target.startsWith("track:") ? Number(target.slice(6)) : null,
    roster_subject_id: target.startsWith("student:") ? target.slice(8) : null,
    start: a, end: b,
  };
}

export function useScopeCoverage(run: string | null | undefined, target: string, start: string, end: string, offset?: number) {
  const key = JSON.stringify([run, target, start, end, offset]);
  const [result, setResult] = useState<{key: string; coverage?: Coverage | null}>();
  useEffect(() => {
    if (!run || offset === undefined) return;
    let active = true;
    const load = async () => {
      try {
        const result = await getScope(run, rangeFilters(target, start, end, offset));
        if (active) setResult({key, coverage: result.coverage});
      } catch {
        if (active) setResult({key, coverage: null});
      }
    };
    void load();
    return () => { active = false; };
  }, [run, target, start, end, offset, key]);
  return result?.key === key ? result.coverage : undefined;
}

export function useAnalysisScope(
  initialTarget = "",
  restoreReportScope = true,
  initialRange?: { start: number; end: number },
  runOverride?: string | null,
) {
  const s = useSession();
  const run = runOverride === undefined ? s.data.job?.run_id : runOverride;
  const [target, setTarget] = useState(initialTarget);
  const [documentIds, setDocumentIds] = useState<string[]>([]);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [students, setStudents] = useState<Student[]>([]);
  const [materialMode, setMaterialMode] = useState<
    "linked"| "selected"| "none"
  >("linked");
  const [error, setError] = useState("");
  const [start, setStart] = useState(initialRange?.start.toString() ?? "");
  const [end, setEnd] = useState(initialRange?.end.toString() ?? "");

  useEffect(() => {
    let active = true;
    void listMaterials()
      .then((rows) => { if (active) setMaterials(rows); })
      .catch((e) => { if (active) setError(String(e)); });
    setStudents([]);
    if (run) {
      void getPlaybackIdentities(run)
        .then(async (r) => {
          const students = r.classroom ? (await getClassroom(r.classroom.classroom_id)).students : [];
          if (active) setStudents(students);
        })
        .catch((e) => { if (active) setError(String(e)); });
    }
    return () => { active = false; };
  }, [run]);

  const applied = useRef(false);
  useEffect(() => {
    if (
      !restoreReportScope ||
      applied.current ||
      s.restoring ||
      s.data.report?.status !== "current"
    )
      return;
    const p = s.data.report.query_plan as
      | {
          document_ids?: string[];
          material_mode?: "linked"| "selected"| "none";
          roster_subject_id?: string;
          track_id?: number;
        }
      | undefined;
    if (p) {
      if (p.document_ids) setDocumentIds(p.document_ids);
      if (p.material_mode) setMaterialMode(p.material_mode);
      if (!initialTarget)
        setTarget(
          p.roster_subject_id
            ? "student:"+ p.roster_subject_id
            : p.track_id
              ? "track:"+ p.track_id
              : "",
        );
      applied.current = true;
    }
  }, [s.restoring, s.data.report, restoreReportScope]);

  function payload() {
    applied.current = true;
    return {
      ...rangeFilters(target, start, end, s.data.scope?.start ?? 0),
      subject_id: null,
      document_ids: documentIds,
      material_mode: materialMode,
    };
  }

  return {
    target,
    setTarget,
    documentIds,
    setDocumentIds,
    materials,
    students,
    materialMode,
    setMaterialMode,
    error,
    start,
    setStart,
    end,
    setEnd,
    payload,
  };
}

export function AnalysisScope({
  scope,
  showPerson = true,
  showVideo = true,
}: {
  scope: ReturnType<typeof useAnalysisScope>;
  showPerson?: boolean;
  showVideo?: boolean;
}) {
  const s = useSession();

  return (
    <fieldset
      disabled={s.restoring}
      className="min-w-0 space-y-3 border-0 p-0 m-0"
    >
      {showPerson && (
        <label className={labelClass}>
          分析对象
          <select
            className={selectClass}
            aria-label="分析对象"
            value={scope.target}
            onChange={(e) => scope.setTarget(e.target.value)}
          >
            <option value="">当前课堂全部范围</option>
            {scope.students.length > 0 && (
              <optgroup label="班级人物">
                {scope.students.map((p) => (
                  <option key={p.subject_id} value={"student:"+ p.subject_id}>
                    {p.display_name}
                  </option>
                ))}
              </optgroup>
            )}
            {showVideo && <optgroup label="匿名人物轨迹">
              {s.data.tracks.map((t) => (
                <option key={t.track_id} value={"track:"+ t.track_id}>
                  人物{String(t.track_id).padStart(3, "0")}
                </option>
              ))}
            </optgroup>}
          </select>
        </label>
      )}

      {showVideo && <div className="grid grid-cols-2 gap-2.5">
        <label className={labelClass}>
          起始秒（可选）
          <input
            className={fieldClass}
            aria-label="分析起始秒"
            type="number"
            step="any"
            min={0}
            value={scope.start}
            onChange={(e) => scope.setStart(e.target.value)}
          />
        </label>
        <label className={labelClass}>
          结束秒（可选）
          <input
            className={fieldClass}
            aria-label="分析结束秒"
            type="number"
            step="any"
            min={0}
            value={scope.end}
            onChange={(e) => scope.setEnd(e.target.value)}
          />
        </label>
      </div>}

      <label className={labelClass}>
        联合资料
        <select
          className={selectClass}
          aria-label="联合资料方式"
          value={scope.materialMode}
          onChange={(e) =>
            scope.setMaterialMode(e.target.value as typeof scope.materialMode)
          }
        >
          <option value="linked">使用当前课堂已关联资料</option>
          <option value="selected">仅使用我选择的资料</option>
          <option value="none">本次仅看视频与课堂记忆</option>
        </select>
      </label>

      {scope.materialMode === "selected"&& (
        <div className="max-h-48 space-y-1.5 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50/70 p-2.5">
          {scope.materials
            .filter((d) => d.source_type !== "classroom_memory")
            .map((d) => (
              <label
                key={d.document_id}
                className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-white:bg-slate-800"
              >
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={scope.documentIds.includes(d.document_id)}
                  onChange={(e) =>
                    scope.setDocumentIds((ids) =>
                      e.target.checked
                        ? [...ids, d.document_id].slice(0, 10)
                        : ids.filter((id) => id !== d.document_id),
                    )
                  }
                />
                <span className="min-w-0">
                  <strong className="block truncate font-medium text-slate-800">
                    {d.title}
                  </strong>
                  <small className="text-slate-500">
                    {materialNames[d.source_type] || "教学资料"}
                  </small>
                </span>
              </label>
            ))}
          {!scope.materials.length && (
            <p className="text-xs text-slate-500">
              还没有资料，可先到教学资料库上传。
            </p>
          )}
        </div>
      )}

      {scope.error ? <AlertBanner tone="danger">{scope.error}</AlertBanner> : null}
    </fieldset>
  );
}

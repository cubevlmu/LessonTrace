import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { z } from "zod";
import {
  SlidersHorizontal,
  SquarePen,
  ArrowUp,
  Sparkles,
  PanelLeft,
  Loader2,
  X,
  RotateCcw,
  BookmarkPlus,
  FilePlus2,
  ExternalLink,
} from "lucide-react";
import { trackLabel } from "../models/view";
import { useSession } from "../state/session";
import { askReview, getReportPlayback, type PlaybackTarget } from "../api/review";
import { ApiError, request } from "../api/client";
import { getClassroom } from "../api/classrooms";
import { confirmLessonRelation, getLessonIndex, revokeLessonRelation, type LessonIndex, type LessonRelation } from "../api/lessonGraph";
import {
  adoptAnswerToReport,
  createLessonReportFromAnswer,
  getLessonReport,
  listLessonReports,
  scopeMismatchDetailSchema,
  type AdoptionResult,
  type LessonReport,
  type ScopeMismatchDetail,
} from "../api/lessonReports";
import { reportSchema, type Playback, type Report } from "../models/backend";
import {
  memories,
  remember,
  deleteMaterial,
  detailSchema,
} from "../api/materials";
import { ReportContent } from "../components/ReportContent";
import { AnalysisScope, formatCoverage, useAnalysisScope, useScopeCoverage } from "../components/AnalysisScope";
import { EvidencePreviewDrawer, playbackReasons } from "../components/EvidencePreviewDrawer";
import { AlertBanner, btnClass, cn, selectClass } from "../components/ui/primitives";

type Turn = { question: string; report: Report };
type Clarification = { subjectId: string; name: string; reportId: string };
type TurnAdoptionState = {
  busy?: boolean;
  result?: AdoptionResult;
  mismatch?: ScopeMismatchDetail;
  error?: string;
};
const angleLabels: Record<LessonReport["angle"], string> = {
  lesson: "整体复盘",
  students: "群体观察",
  student: "个人复盘",
};
function draftSummary(draft: LessonReport) {
  const scopeLabel =
    draft.product_scope?.subject_name ||
    (draft.options.track_id ? trackLabel(draft.options.track_id) : "全录像");
  return `${angleLabels[draft.angle]} · ${scopeLabel} · 第${draft.version}版${draft.status === "stale" ? "（已失效）" : ""}`;
}
const savedChatsSchema = z.array(
  z.object({
    last_report_id: z.string(),
    title: z.string(),
    turn_count: z.number(),
    updated_at: z.string(),
  }),
);
const turnsSchema = z.array(z.object({ question: z.string(), report: reportSchema }));

const starterQuestions = [
  "结合教案和作业，总结本段课堂的观察与建议。",
  "当前人物有哪些可观察行为？",
  "仅凭这段画面能证明学生没听懂吗？证据边界是什么？",
  "接下来教师可以重点跟进什么？",
];
const materialQuestions = [
  "结合教案与作业，总结这节课的学习表现。",
  "学生在作业中有哪些已经做对的地方？",
  "现有资料能说明什么，还有哪些不能判断？",
  "下一次课可以重点跟进什么？",
];
const rangeParams = ["run", "track", "start", "end"] as const;
type ClipRange = {track: number; start: number; end: number};
type Preview = {
  ticket: number;
  trigger: HTMLButtonElement;
  playback: Extract<Playback, {status: "available"}> | null;
};

export function ChatPage() {
  const s = useSession();
  const [params, setSearchParams] = useSearchParams();
  const run = s.data.job?.run_id;
  const deepLinked = rangeParams.some((key) => params.has(key));
  const lessonId = params.get("lesson")?.trim() || undefined;
  if (deepLinked && s.restoring)
    return (
      <p role="status" className="p-6 text-sm text-[#6e5a74]">
        正在恢复证据所属课堂…
      </p>
    );
  const secondsOrId = (key: string) => {
    const value = params.get(key)?.trim();
    return value ? Number(value) : NaN;
  };
  const track = secondsOrId("track");
  const start = secondsOrId("start");
  const end = secondsOrId("end");
  const clip = s.data.scope;
  if (deepLinked && (
    !run ||
    params.get("run") !== run ||
    !clip ||
    !s.data.tracks.some((person) => person.track_id === track) ||
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    start < 0 ||
    end < start ||
    end > clip.end - clip.start + 0.002
  )) {
    return (
      <AlertBanner
        tone="danger"
        action={
          <Link className={btnClass("secondary", "sm")} to="/review">
            返回工作台
          </Link>
        }
      >
        当前课堂无法定位这条证据。
      </AlertBanner>
    );
  }
  return (
    <Conversation
      key={lessonId ? "lesson:" + lessonId : "run:" + (run ?? "")}
      lessonId={lessonId}
      linkedRange={deepLinked ? {track, start, end} : undefined}
      clearDeepLink={() => {
        if (!deepLinked) return;
        const next = new URLSearchParams(params);
        rangeParams.forEach((key) => next.delete(key));
        setSearchParams(next, {replace: true});
      }}
    />
  );
}

function Conversation({
  lessonId, linkedRange, clearDeepLink,
}: {
  lessonId?: string;
  linkedRange?: ClipRange;
  clearDeepLink: () => void;
}) {
  const s = useSession();
  const [lessonIndex, setLessonIndex] = useState<LessonIndex>();
  const [classroom, setClassroom] = useState<Awaited<ReturnType<typeof getClassroom>>>();
  const [relationBusy, setRelationBusy] = useState(false);
  const [relationError, setRelationError] = useState("");
  const job = s.data.job;
  // A lesson in the URL must not borrow the run of an unrelated recording.
  const run = lessonId
    ? (lessonIndex?.video_id && job?.video_id === lessonIndex.video_id && job.stage !== "tracking" ? job.run_id : null)
    : job?.run_id;
  const scopeParams = lessonId ? { lesson_id: lessonId } : run ? { run_id: run } : undefined;
  const scopeQuery = lessonId ? "lesson_id=" + encodeURIComponent(lessonId) :
    run ? "run_id=" + encodeURIComponent(run) : "";
  const linkedTrack = linkedRange?.track;
  const linkedStart = linkedRange?.start;
  const linkedEnd = linkedRange?.end;
  const scope = useAnalysisScope(
    linkedTrack ? "track:" + linkedTrack : "",
    false,
    linkedRange,
    run ?? null,
  );
  const {setTarget, setStart, setEnd} = scope;
  useLayoutEffect(() => {
    setTarget(linkedTrack === undefined ? "" : "track:" + linkedTrack);
    setStart(linkedStart?.toString() ?? "");
    setEnd(linkedEnd?.toString() ?? "");
  }, [linkedTrack, linkedStart, linkedEnd, setTarget, setStart, setEnd]);
  const coverage = useScopeCoverage(run, scope.target, scope.start, scope.end, s.data.scope?.start);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewIssues, setPreviewIssues] = useState(new Map<HTMLButtonElement, string>());
  const previewGeneration = useRef(0);
  const [question, setQuestion] = useState("");
  const [chosenStudent, setChosenStudent] = useState<{id: string; name: string}>();
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [clarificationRetry, setClarificationRetry] = useState<{question: string; candidate: Clarification}>();
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState("");
  const [memory, setMemory] = useState<z.infer<typeof detailSchema>[]>([]);
  const [saveMemory, setSaveMemory] = useState(false);
  const [useMemory, setUseMemory] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);
  const [savedChats, setSavedChats] = useState<
    z.infer<typeof savedChatsSchema>
  >([]);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [drafts, setDrafts] = useState<LessonReport[]>([]);
  const [selectedDraftId, setSelectedDraftId] = useState("");
  const [adoptions, setAdoptions] = useState<Record<string, TurnAdoptionState>>({});

  const historyGeneration = useRef(0);
  const draftsGeneration = useRef(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const runRef = useRef(run);
  const activeScope = useRef(false);

  useEffect(() => {
    if (!lessonId || s.restoring) return;
    let active = true;
    void getLessonIndex(lessonId)
      .then((index) => { if (active) setLessonIndex(index); })
      .catch(() => { if (active) setRelationError("暂时无法读取课次的待确认归属，不影响提问。"); });
    return () => { active = false; };
  }, [lessonId, s.restoring]);

  const classroomId = lessonIndex?.classroom_id;
  useEffect(() => {
    if (!classroomId) return;
    let active = true;
    void getClassroom(classroomId)
      .then((doc) => { if (active) setClassroom(doc); })
      .catch(() => {});
    return () => { active = false; };
  }, [classroomId]);

  async function decideRelation(relation: LessonRelation, confirm: boolean) {
    if (!lessonId || !s.canWrite || relationBusy) return;
    setRelationBusy(true);
    setRelationError("");
    try {
      if (confirm) await confirmLessonRelation(lessonId, relation.relation_id, relation.rev);
      else await revokeLessonRelation(lessonId, relation.relation_id, "不是此人", relation.rev);
      // Do not offer the same decision again if refreshing the index fails.
      if (activeScope.current) setLessonIndex((index) => index && ({
        ...index, candidates: index.candidates.filter((candidate) => candidate.relation_id !== relation.relation_id),
      }));
    } catch (e) {
      if (activeScope.current) setRelationError(String(e));
    } finally {
      try {
        const index = await getLessonIndex(lessonId);
        if (activeScope.current) setLessonIndex(index);
      } catch {
        if (activeScope.current) setRelationError((error) => error || "待确认列表暂未刷新。");
      } finally {
        if (activeScope.current) setRelationBusy(false);
      }
    }
  }

  async function loadDrafts(targetRun = runRef.current, preferredId?: string) {
    if (!targetRun) {
      setDrafts([]);
      setSelectedDraftId("");
      return;
    }
    const ticket = ++draftsGeneration.current;
    try {
      const rows = await listLessonReports(targetRun);
      const loaded: LessonReport[] = [];
      for (const row of rows) {
        try {
          loaded.push(await getLessonReport(row.report_id));
        } catch {
          // Ignore unreadable or removed draft entries.
        }
      }
      if (ticket !== draftsGeneration.current || runRef.current !== targetRun) return;
      setDrafts(loaded);
      setSelectedDraftId((prev) => {
        const pick = preferredId || prev;
        if (pick && loaded.some((d) => d.report_id === pick && d.status === "current")) {
          return pick;
        }
        return loaded.find((d) => d.status === "current")?.report_id ?? "";
      });
    } catch {
      // Non-blocking: draft list may be empty or mocked out in isolated tests.
    }
  }

  function updateAdoption(fid: string, patch: Partial<TurnAdoptionState>) {
    setAdoptions((prev) => ({ ...prev, [fid]: { ...prev[fid], ...patch } }));
  }

  async function handleCreateFromAnswer(
    fid: string,
    createReason: "first_draft" | "scope_mismatch",
    targetReportId?: string,
  ) {
    updateAdoption(fid, { busy: true, error: "", mismatch: undefined });
    try {
      const res = await createLessonReportFromAnswer({
        fusion_report_id: fid,
        create_reason: createReason,
        ...(targetReportId ? { target_report_id: targetReportId } : {}),
      });
      updateAdoption(fid, { busy: false, result: res, mismatch: undefined, error: "" });
      await loadDrafts(runRef.current, res.report_id);
    } catch (e) {
      if (
        e instanceof ApiError &&
        e.status === 409 &&
        e.detail &&
        typeof e.detail === "object" &&
        "code" in e.detail &&
        (e.detail as { code?: unknown }).code === "draft_exists"
      ) {
        await loadDrafts(runRef.current);
        updateAdoption(fid, {
          busy: false,
          error: "当前录像已有可用报告草稿，请先选择目标草稿后再加入。",
        });
        return;
      }
      updateAdoption(fid, { busy: false, error: String(e) });
    }
  }

  async function handleAdopt(fid: string) {
    const target = drafts.find((d) => d.report_id === selectedDraftId && d.status === "current");
    if (!target) {
      await handleCreateFromAnswer(fid, "first_draft");
      return;
    }
    updateAdoption(fid, { busy: true, error: "", mismatch: undefined });
    try {
      const res = await adoptAnswerToReport(target.report_id, fid, target.version);
      updateAdoption(fid, { busy: false, result: res, mismatch: undefined, error: "" });
      await loadDrafts(runRef.current, res.report_id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const parsed = scopeMismatchDetailSchema.safeParse(e.detail);
        if (parsed.success) {
          updateAdoption(fid, { busy: false, mismatch: parsed.data, error: "" });
          return;
        }
      }
      updateAdoption(fid, { busy: false, error: String(e) });
    }
  }

  function previewIssue(trigger: HTMLButtonElement, message: string) {
    setPreviewIssues((issues) => {
      if (!message && !issues.has(trigger)) return issues;
      const next = new Map(issues);
      if (message) next.set(trigger, message);
      else next.delete(trigger);
      return next;
    });
  }

  function closePreview(trigger = preview?.trigger) {
    previewGeneration.current++;
    setPreview(null);
    if (trigger?.isConnected) trigger.focus({preventScroll: true});
  }

  async function openPreview(reportId: string, target: PlaybackTarget, trigger: HTMLButtonElement) {
    const ticket = ++previewGeneration.current;
    previewIssue(trigger, "");
    setPreview({ticket, trigger, playback: null});
    try {
      const playback = await getReportPlayback(reportId, target);
      if (ticket !== previewGeneration.current) return;
      if (playback.status === "available") setPreview({ticket, trigger, playback});
      else {
        previewIssue(trigger, playbackReasons[playback.reason]);
        closePreview(trigger);
      }
    } catch (error) {
      if (ticket !== previewGeneration.current) return;
      previewIssue(trigger, "无法核对录像来源。 " + String(error));
      closePreview(trigger);
    }
  }

  async function loadHistory(last?: string) {
    if (!scopeQuery) return;
    const ticket = ++historyGeneration.current;
    setLoading(true);
    setHistoryError("");
    const [values, chats] = await Promise.allSettled([
      request<Turn[]>("fusion/conversation?" + scopeQuery +
        (last ? "&last_report_id=" + encodeURIComponent(last) : ""), turnsSchema),
      request("fusion/conversations?" + scopeQuery, savedChatsSchema),
    ]);
    if (ticket !== historyGeneration.current) return;
    if (values.status === "fulfilled") setTurns(values.value);
    if (chats.status === "fulfilled") setSavedChats(chats.value);
    if (values.status === "rejected" || chats.status === "rejected") {
      setHistoryError("暂时无法恢复完整的历史对话，仍可继续提问。");
    }
    setLoading(false);
  }

  useEffect(() => {
    runRef.current = run;
    setDrafts([]);
    setSelectedDraftId("");
    setMemory([]);
    if (run) {
      void loadDrafts(run);
      void memories(run).then((notes) => {
        if (runRef.current === run) setMemory(notes);
      }).catch(() => {});
    }
    return () => {
      draftsGeneration.current++;
      runRef.current = undefined;
    };
  }, [run]);

  useEffect(() => {
    activeScope.current = true;
    if (!s.restoring) void loadHistory();
    return () => {
      activeScope.current = false;
      historyGeneration.current++;
      previewGeneration.current++;
    };
  }, [scopeQuery, s.restoring]);

  async function selectChat(last?: string) {
    if (!scopeParams) return;
    closePreview();
    setPreviewIssues(new Map());
    setLoading(true);
    setError("");
    setClarificationRetry(undefined);
    try {
      await request(
        "fusion/conversation/select",
        z.object({ saved: z.boolean() }),
        { ...scopeParams, last_report_id: last || null },
      );
      setQuestion("");
      setPending("");
      await loadHistory(last);
    } catch (e) {
      setError(String(e));
      setLoading(false);
    }
  }

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [turns.length, pending, busy]);

  async function send(q = question, clarification?: Clarification) {
    if (!scopeParams || busy || loading || s.restoring || !q.trim()) return;
    if (clarification) {
      setChosenStudent({id: clarification.subjectId, name: clarification.name});
      setTarget("student:" + clarification.subjectId);
    }
    setBusy(true);
    setPending(q);
    if (!clarification) setQuestion("");
    setError("");
    setClarificationRetry(undefined);
    setMessage("");
    try {
      const selection = scope.payload();
      if (clarification) {
        selection.roster_subject_id = clarification.subjectId;
        selection.track_id = null;
      }
      if (!run) {
        selection.track_id = null;
        selection.start = null;
        selection.end = null;
      }
      let prompt = q;
      const command = /^(?:记住|记忆|补充课堂信息)\s*[:：]/.test(q);
      if (run && !clarification && (saveMemory || command)) {
        await remember({
          run_id: run,
          text: q.replace(/^(?:记住|记忆|补充课堂信息)\s*[:：]\s*/, ""),
          subject_id: selection.roster_subject_id,
          track_id: selection.track_id,
          supersedes: editing,
        });
        setMemory(await memories(run));
        setMessage("课堂补充已保存，后续回答会使用最新记录。");
        setSaveMemory(false);
        setEditing(null);
        if (command)
          prompt =
            "结合刚保存的教师课堂补充与现有资料，说明当前可观察记录和教学建议。";
      }
      const previous = turns[turns.length - 1]?.report;
      const report = await askReview({
        ...scopeParams,
        ...(lessonId && run ? { run_id: run } : {}),
        conversation: true,
        previous_report_id:
          clarification?.reportId ?? (previous?.status !== "stale" ? previous?.report_id : null),
        question: prompt,
        ...selection,
        use_classroom_memory: useMemory,
        use_llm: true,
        allow_verification: false,
        budget_seconds: 180,
        max_model_calls: 2,
      });
      if (!activeScope.current) return;
      if (!lessonId) s.setReport(report);
      setTurns((t) => [...t, { question: q, report }]);
      setPending("");
      void request(
        "fusion/conversations?" + scopeQuery,
        savedChatsSchema,
      )
        .then((chats) => { if (activeScope.current) setSavedChats(chats); })
        .catch(() => {});
    } catch (e) {
      if (activeScope.current) {
        setError(String(e));
        if (clarification) setClarificationRetry({question: q, candidate: clarification});
        else setQuestion(q);
        setPending("");
      }
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!window.confirm("撤销这条课堂补充？相关旧报告会失效。")) return;
    try {
      await deleteMaterial(id);
      setMemory(await memories(s.data.job!.run_id!));
      setMessage("已撤销补充，后续分析使用最新资料。");
    } catch (e) {
      setError(String(e));
    }
  }

  const students = classroom?.students ?? scope.students;
  const person = scope.target.startsWith("track:")
    ? trackLabel(Number(scope.target.slice(6)))
    : students.find((p) => "student:" + p.subject_id === scope.target)?.display_name ||
      (chosenStudent && scope.target === "student:" + chosenStudent.id ? chosenStudent.name : lessonIndex?.title || "当前课堂");
  const period =
    scope.start && scope.start === scope.end
      ? `视频内第 ${scope.start} 秒`
      : `视频内 ${scope.start || "0"}–${scope.end || "结尾"} 秒`;
  const scopeSummary =
    scope.start || scope.end ? person + " · " + period : person;

  return (
    <div className="relative flex h-[calc(100vh-5rem)] overflow-hidden rounded-2xl border border-[#e8dce9] bg-white shadow-[0_2px_12px_-6px_rgba(74,21,59,0.08)]">
      {/* Left Collapsible History Rail (ChatGPT / Claude Style) */}
      {historyOpen && (
        <aside
          aria-label="历史对话"
          className="hidden w-56 shrink-0 flex-col border-r border-[#efe4f1] bg-[#fcf9fd] md:flex"
        >
          <div className="p-2.5">
            <button
              type="button"
              disabled={busy || loading || s.restoring || !scopeParams}
              onClick={() => void selectChat()}
              className="flex w-full items-center justify-center gap-2 rounded-xl border border-[#e3bed8] bg-white px-3 py-2 text-xs font-semibold text-[#992777] shadow-2xs transition hover:border-[#992777] hover:bg-[#faf0f7] disabled:opacity-45"
            >
              <SquarePen size={14} />
              新对话
            </button>
          </div>

          <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-[#9c89a2]">
            历史对话
          </div>

          <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
            {savedChats.map((chat) => {
              const active =
                turns[turns.length - 1]?.report.report_id ===
                chat.last_report_id;
              return (
                <button
                  type="button"
                  key={chat.last_report_id}
                  disabled={busy || loading}
                  onClick={() => void selectChat(chat.last_report_id)}
                  className={cn(
                    "w-full rounded-lg px-2.5 py-2 text-left transition",
                    active
                      ? "bg-[#f4e4f0] text-[#7f1e62]"
                      : "text-[#4a3450] hover:bg-[#f5eff7]",
                  )}
                >
                  <strong className="block truncate text-xs font-medium">
                    {chat.title}
                  </strong>
                  <small className="mt-0.5 block text-[10px] text-[#85708c]">
                    {chat.turn_count}轮 ·{" "}
                    {new Date(chat.updated_at).toLocaleDateString("zh-CN")}
                  </small>
                </button>
              );
            })}
            {!savedChats.length && (
              <p className="px-2 py-8 text-center text-xs text-[#9c89a2]">
                发送问题后，记录会保存在这里。
              </p>
            )}
          </div>
        </aside>
      )}

      {/* Main Chat Canvas (Claude / Gemini / ChatGPT Style) */}
      <section
        aria-label="课堂对话"
        className="flex min-w-0 flex-1 flex-col bg-white"
      >
        {/* Minimal Top Header */}
        <header className="flex items-center justify-between gap-2 border-b border-[#f3eaf5] px-4 py-2.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <button
              type="button"
              aria-label="切换历史对话栏"
              onClick={() => setHistoryOpen((v) => !v)}
              className="hidden h-7 w-7 items-center justify-center rounded-lg text-[#6e5a74] hover:bg-[#f7eaf3] hover:text-[#992777] md:inline-flex"
            >
              <PanelLeft size={16} />
            </button>
            <h1 className="text-sm font-bold text-[#231426]">课堂助手</h1>
            <span className="truncate rounded-full bg-[#f7eaf3] px-2.5 py-0.5 text-[11px] font-medium text-[#841f66]">
              {scopeSummary}
            </span>
            {run && <span className="text-[11px] text-slate-500">{formatCoverage(coverage)}</span>}
            {(linkedRange || scope.target || scope.start || scope.end) && (
              <button
                type="button"
                className={btnClass("ghost", "sm")}
                onClick={() => {
                  setTarget("");
                  setStart("");
                  setEnd("");
                  clearDeepLink();
                }}
              >
                {run ? "当前录像全部已分析范围" : "当前课次全部范围"}
              </button>
            )}
          </div>

          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className={btnClass("ghost", "sm")}
              onClick={() => dialog.current?.showModal()}
            >
              <SlidersHorizontal size={13} />
              范围与资料
            </button>
            {!historyOpen && (
              <button
                type="button"
                className={btnClass("secondary", "sm")}
                disabled={busy || loading || s.restoring || !scopeParams}
                onClick={() => void selectChat()}
              >
                <SquarePen size={13} />
                新对话
              </button>
            )}
          </div>
        </header>

        {/* Scrollable Message Stream */}
        <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8" aria-live="polite">
          <div className="mx-auto max-w-3xl space-y-6">
            {loading && (
              <div
                role="status"
                className="flex items-center justify-center gap-2 py-12 text-xs text-[#6e5a74]"
              >
                <Loader2 size={15} className="animate-spin text-[#992777]" />
                正在恢复对话…
              </div>
            )}

            {/* Empty Welcome View (Claude / Gemini style) */}
            {!loading && !turns.length && !pending && (
              <div className="mx-auto flex max-w-2xl flex-col items-center pt-10 pb-6 text-center">
                <h2 className="text-2xl font-bold tracking-tight text-[#231426]">
                  今天想了解课堂中的什么？
                </h2>
                <p className="mt-2 text-sm text-[#6e5a74]">
                  {lessonId && !run ? "可以先围绕教案、作业和批改记录提问，无需等待录像分析。" :
                    "询问某位学生的外显动作、证据边界，或结合教案与作业生成复盘建议。"}
                </p>
                <div className="mt-7 grid w-full gap-2.5 sm:grid-cols-2">
                  {(lessonId && !run ? materialQuestions : starterQuestions).map((q) => (
                    <button
                      type="button"
                      key={q}
                      disabled={s.restoring || !scopeParams}
                      onClick={() => void send(q)}
                      className="group flex items-start gap-2.5 rounded-2xl border border-[#ebdce9] bg-[#fcf9fd] px-4 py-3.5 text-left text-xs leading-relaxed font-medium text-[#3d2542] transition hover:border-[#cfa1c3] hover:bg-[#f7eaf3]/70 hover:text-[#6e1a55] disabled:opacity-45"
                    >
                      <Sparkles
                        size={14}
                        className="mt-0.5 shrink-0 text-[#992777] opacity-75 group-hover:opacity-100"
                      />
                      <span>{q}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Conversation Turns */}
            {turns.map((t, i) => (
              <article key={t.report.report_id + ":" + i} className="space-y-4">
                {/* User Message */}
                <div className="flex justify-end">
                  <div className="max-w-[82%] rounded-2xl rounded-br-md bg-[#f4e4f0] px-4 py-2.5 text-sm leading-relaxed text-[#231426]">
                    <p className="whitespace-pre-wrap">{t.question}</p>
                  </div>
                </div>

                {/* Assistant Message (Clean editorial flow, no heavy box) */}
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#992777] text-[11px] font-bold text-white shadow-2xs">
                    课迹
                  </span>
                  <div className="min-w-0 flex-1 space-y-2.5 pt-0.5">
                    <ReportContent report={t.report} onPreview={openPreview} previewIssues={previewIssues}
                      onSelectCandidate={busy || loading || s.restoring || t.report.status === "stale" ? undefined : (subjectId) => {
                        const candidate = t.report.clarification_candidates?.find((item) => item.subject_id === subjectId);
                        if (candidate) void send(t.question, {subjectId, name: candidate.display_name, reportId: t.report.report_id});
                      }} />
                    {s.canWrite && t.report.status !== "stale" && !t.report.clarification_candidates?.length && (() => {
                      const fid = t.report.report_id;
                      const state = adoptions[fid] ?? {};
                      const currentDrafts = drafts.filter((d) => d.status === "current");
                      const targetDraft = currentDrafts.find((d) => d.report_id === selectedDraftId) ?? currentDrafts[0];
                      const onlyStale = drafts.length > 0 && currentDrafts.length === 0;
                      return (
                        <div className="space-y-2 rounded-xl border border-[#efe4f1] bg-[#fcf9fd] px-3 py-2 text-xs">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex flex-wrap items-center gap-2 text-[#4a3450]">
                              {targetDraft ? (
                                <>
                                  <span>目标草稿：{draftSummary(targetDraft)}</span>
                                  {drafts.length > 1 && (
                                    <select
                                      aria-label="选择目标报告草稿"
                                      className={cn(selectClass, "h-7 py-0 text-xs")}
                                      value={targetDraft.report_id}
                                      disabled={state.busy}
                                      onChange={(e) => {
                                        setSelectedDraftId(e.target.value);
                                        updateAdoption(fid, { mismatch: undefined, error: "" });
                                      }}
                                    >
                                      {drafts.map((d) => (
                                        <option key={d.report_id} value={d.report_id} disabled={d.status !== "current"}>
                                          {draftSummary(d)}
                                        </option>
                                      ))}
                                    </select>
                                  )}
                                </>
                              ) : onlyStale ? (
                                <span className="text-amber-800">
                                  当前录像的已有草稿已失效{drafts[0]?.reason ? `（${drafts[0].reason}）` : ""}，无法继续写入。
                                </span>
                              ) : (
                                <span className="text-[#6e5a74]">{lessonId && !run ? "本课次尚无报告草稿" : "本录像尚无报告草稿"}</span>
                              )}
                            </div>

                            <div className="flex flex-wrap items-center gap-2">
                              {targetDraft ? (
                                <button
                                  type="button"
                                  className={btnClass("secondary", "sm")}
                                  disabled={state.busy}
                                  onClick={() => void handleAdopt(fid)}
                                >
                                  <FilePlus2 size={13} />
                                  {state.busy ? "正在加入…" : "加入报告草稿"}
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  className={btnClass("secondary", "sm")}
                                  disabled={state.busy}
                                  onClick={() => void handleCreateFromAnswer(fid, "first_draft")}
                                >
                                  <FilePlus2 size={13} />
                                  {state.busy
                                    ? "正在建稿…"
                                    : onlyStale
                                      ? "按本回答范围另建草稿"
                                      : "创建草稿并加入"}
                                </button>
                              )}
                            </div>
                          </div>

                          {state.result && (
                            <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[#f4e4f0] px-2.5 py-1.5 text-[#6e1a55]">
                              <span>
                                {state.result.outcome === "already_adopted"
                                  ? `本回答已加入该草稿（第 ${state.result.adopted_version} 版）`
                                  : state.result.created
                                    ? `已创建报告草稿并加入（第 ${state.result.version} 版）`
                                    : `已加入报告草稿（第 ${state.result.version} 版）`}
                              </span>
                              <Link
                                to={"/report?" + new URLSearchParams({
                                  rid: state.result.report_id, ...(lessonId ? { lesson: lessonId } : {}),
                                })}
                                className="inline-flex items-center gap-1 font-semibold text-[#992777] underline-offset-2 hover:underline"
                              >
                                打开草稿
                                <ExternalLink size={12} />
                              </Link>
                            </div>
                          )}

                          {state.mismatch && (
                            <div role="alert" className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-amber-900">
                              <p className="font-semibold">当前目标草稿与本回答范围或来源不一致，旧稿已保留：</p>
                              <ul className="list-disc space-y-0.5 pl-4">
                                {state.mismatch.reasons.map((r, idx) => (
                                  <li key={idx}>{r.message}</li>
                                ))}
                              </ul>
                              <div className="flex flex-wrap items-center gap-2 pt-1">
                                {state.mismatch.can_create_from_answer && (
                                  <button
                                    type="button"
                                    className={btnClass("primary", "sm")}
                                    disabled={state.busy}
                                    onClick={() =>
                                      void handleCreateFromAnswer(fid, "scope_mismatch", state.mismatch!.report_id)
                                    }
                                  >
                                    按本回答范围另建草稿（保留旧稿）
                                  </button>
                                )}
                                {currentDrafts.length > 1 && (
                                  <span className="text-[11px] text-amber-800">
                                    也可以在上方切换其他目标草稿后重试。
                                  </span>
                                )}
                              </div>
                            </div>
                          )}

                          {state.error && (
                            <p role="alert" className="text-rose-700">{state.error}</p>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                </div>
              </article>
            ))}

            {(!!lessonIndex?.candidates.some((relation) => relation.status === "candidate") || relationError) && (
              <section aria-label="待确认归属" className="space-y-2 rounded-xl border border-[#e8dce9] bg-[#fcf9fd] p-3 text-xs">
                <h2 className="font-semibold text-[#841f66]">待确认归属</h2>
                <p className="text-[#6e5a74]">待确认的资料不会作为该生的依据，可以继续提问。</p>
                <ul className="space-y-2">
                  {lessonIndex?.candidates.filter((relation) => relation.status === "candidate").map((relation) => {
                    const document = lessonIndex.documents.find((doc) => doc.document_id === relation.src_id);
                    const student = students.find((person) => person.subject_id === relation.dst_id);
                    return (
                      <li key={relation.relation_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-white px-2.5 py-2">
                        <span className="text-[#4a3450]">
                          {document?.title || "待归属资料 " + relation.src_id} · {student?.display_name || "学生 " + relation.dst_id}
                        </span>
                        {s.canWrite && <div className="flex flex-wrap gap-1">
                          <button type="button" className={btnClass("secondary", "sm")} disabled={relationBusy}
                            onClick={() => void decideRelation(relation, true)}>确认归属</button>
                          <button type="button" className={btnClass("ghost", "sm")} disabled={relationBusy}
                            onClick={() => void decideRelation(relation, false)}>不是此人（撤销）</button>
                        </div>}
                      </li>
                    );
                  })}
                </ul>
                {relationError && <p role="status" className="text-[#6e5a74]">{relationError}</p>}
              </section>
            )}

            {pending && (
              <div className="flex justify-end">
                <div className="max-w-[82%] rounded-2xl rounded-br-md bg-[#f4e4f0] px-4 py-2.5 text-sm leading-relaxed text-[#231426]">
                  <p className="whitespace-pre-wrap">{pending}</p>
                </div>
              </div>
            )}

            {busy && (
              <div className="flex items-center gap-3">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#992777] text-[11px] font-bold text-white">
                  课迹
                </span>
                <div
                  role="status"
                  className="flex items-center gap-2 text-xs text-[#6e5a74]"
                >
                  <Loader2 size={14} className="animate-spin text-[#992777]" />
                  正在阅读课堂背景与相关资料…
                </div>
              </div>
            )}

            {historyError && <p role="status" className="text-xs text-[#6e5a74]">{historyError}</p>}
            {error ? (
              <AlertBanner
                tone="danger"
                action={
                  <button
                    type="button"
                    className={btnClass("ghost", "sm")}
                    disabled={busy}
                    onClick={() => void (clarificationRetry
                      ? send(clarificationRetry.question, clarificationRetry.candidate) : send())}
                  >
                    <RotateCcw size={13} />
                    重试
                  </button>
                }
              >
                {error}
              </AlertBanner>
            ) : null}

            {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}
            <div ref={bottom} />
          </div>
        </div>

        {/* Floating Pill Composer (ChatGPT / Claude style) */}
        <div className="px-4 pt-2 pb-4 sm:px-8">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
            className="mx-auto max-w-3xl rounded-2xl border border-[#dfd0e2] bg-white p-2.5 shadow-[0_6px_24px_-10px_rgba(153,39,119,0.18)] transition focus-within:border-[#992777] focus-within:ring-2 focus-within:ring-[#992777]/12"
          >
            <textarea
              aria-label="教师问题"
              rows={2}
              maxLength={1200}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={run ? "问问这节课，或输入“记住：……”补充背景" : "问问这节课的教案、作业或批改记录"}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
              className="w-full resize-none border-0 bg-transparent px-2 py-1 text-sm text-[#231426] placeholder:text-[#9c89a2] focus:outline-none"
            />
            <div className="mt-1.5 flex items-center justify-between gap-2 px-1">
              <div className="flex items-center gap-3">
                {run && <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-[#6e5a74] transition hover:bg-[#faf4fb] hover:text-[#231426]">
                  <input
                    type="checkbox"
                    checked={saveMemory}
                    onChange={(e) => setSaveMemory(e.target.checked)}
                    className="accent-[#992777]"
                  />
                  <BookmarkPlus size={13} className="text-[#992777]" />
                  <span>保存为课堂补充</span>
                </label>}
              </div>
              <div className="flex items-center gap-2.5">
                <span className="hidden sm:inline text-[11px] text-[#9c89a2]">
                  Enter 发送 · Shift+Enter 换行
                </span>
                <button
                  type="submit"
                  aria-label="发送"
                  disabled={
                    busy ||
                    loading ||
                    s.restoring ||
                    !question.trim() ||
                    !scopeParams
                  }
                  className="inline-flex h-8 items-center gap-1 rounded-xl bg-[#992777] px-3 text-xs font-medium text-white shadow-xs transition hover:bg-[#7f1e62] disabled:pointer-events-none disabled:opacity-40"
                >
                  <ArrowUp size={14} />
                  <span>发送</span>
                </button>
              </div>
            </div>
          </form>
        </div>
      </section>

      {preview && (
        <EvidencePreviewDrawer key={preview.ticket} playback={preview.playback}
          onClose={() => closePreview()} onMediaError={(message) => {
            if (preview.ticket === previewGeneration.current) previewIssue(preview.trigger, message);
          }} />
      )}

      {/* Scope & Materials Dialog */}
      <dialog
        ref={dialog}
        className="lt-dialog"
        aria-label="课堂范围与资料"
        onClick={(e) => {
          if (e.target === e.currentTarget) dialog.current?.close();
        }}
      >
        <header className="flex items-center justify-between border-b border-[#e8dce9] px-5 py-4">
          <h2 className="text-base font-semibold text-[#231426]">
            课堂范围与资料
          </h2>
          <button
            type="button"
            className={btnClass("ghost", "sm")}
            onClick={() => dialog.current?.close()}
          >
            <X size={14} />
            关闭
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <AnalysisScope scope={lessonId ? {
            ...scope, students,
            materials: scope.materials.filter((material) => lessonIndex?.documents.some((doc) => doc.document_id === material.document_id)),
          } : scope} showVideo={!!run} />

          {run && <section className="space-y-3 border-t border-[#f0e5f2] pt-4">
            <h3 className="text-sm font-semibold text-[#231426]">
              教师课堂补充
            </h3>
            <label className="flex items-center gap-2 text-xs text-[#4a3450]">
              <input
                type="checkbox"
                checked={useMemory}
                onChange={(e) => setUseMemory(e.target.checked)}
              />
              本次使用已保存的课堂补充
            </label>
            <div className="space-y-2">
              {memory.map((d) => (
                <article
                  key={d.document_id}
                  className="rounded-xl border border-[#e8dce9] bg-[#faf7fb] p-3 text-xs"
                >
                  <p className="whitespace-pre-wrap text-[#4a3450]">
                    {d.passages.map((p) => p.content).join("\n")}
                  </p>
                  {s.canWrite && (
                    <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        className={btnClass("ghost", "sm")}
                        onClick={() => {
                          setQuestion(
                            d.passages.map((p) => p.content).join("\n"),
                          );
                          setSaveMemory(true);
                          setEditing(d.document_id);
                          scope.setTarget(
                            d.scope?.subject_id
                              ? "student:" + d.scope.subject_id
                              : d.scope?.track_id
                                ? "track:" + d.scope.track_id
                                : "",
                          );
                          dialog.current?.close();
                        }}
                      >
                        修改
                      </button>
                      <button
                        type="button"
                        className={btnClass("danger", "sm")}
                        onClick={() => void remove(d.document_id)}
                      >
                        撤销
                      </button>
                    </div>
                  )}
                </article>
              ))}
              {!memory.length && (
                <p className="text-xs text-[#6e5a74]">
                  在对话中输入“记住：……”可明确保存教师补充。
                </p>
              )}
            </div>
          </section>}
        </div>
      </dialog>
    </div>
  );
}

import { useState } from "react";
import { Link } from "react-router-dom";
import { Eye, Clock, ChevronDown, MessageSquareText } from "lucide-react";
import { personLabel, type IdentityLabel } from "../api/workbench";
import { useSession } from "../state/session";
import {
  fmt,
  stateLabel,
  humanReviewLabel,
  trackLabel,
  observationView,
  observationBasis,
  type VisualItem,
} from "../models/view";
import {
  AlertBanner,
  badgeClass,
  btnClass,
  cardClass,
  cardHeadClass,
  cardTitleClass,
  statusTone,
} from "./ui/primitives";

export function StatusChip({ status }: { status: string }) {
  return (
    <span className={badgeClass(statusTone(status))} title={status}>
      {stateLabel[status] || status}
    </span>
  );
}

export function EvidencePanel({
  item,
  onSeek,
  identityLabels = [],
}: {
  item?: VisualItem;
  onSeek: (t: number) => void;
  identityLabels?: IdentityLabel[];
}) {
  const s = useSession();
  const [failed, setFailed] = useState("");
  const view = item ? observationView(item) : null;
  const runId = s.data.job?.run_id;

  return (
    <section className={cardClass}>
      <div className={cardHeadClass}>
        <h2 className={cardTitleClass}>
          <Eye size={15} className="text-[#992777]"/>
          证据面板
        </h2>
        {view ? (
          <span
            className={badgeClass(
              view.source === "教师确认"
                ? "success"
                : view.source === "模型支持"
                  ? "info"
                  : view.source === "教师已驳回"|| view.source === "模型反证"
                    ? "danger"
                    : "warning",
            )}
            title={item?.state}
          >
            {view.source}
          </span>
        ) : null}
      </div>

      <div className="space-y-3 p-4 text-sm">
        {!item || !view ? (
          <p className="py-6 text-center text-xs text-[#6e5a74]">
            请选择观察窗口。没有可靠类别时仍展示实际视觉观察。
          </p>
        ) : (
          <>
            <div className="rounded-xl border border-[#ebdce9] bg-[#faf4f9] p-3">
              <p className="font-semibold text-[#231426]">
                识别状态：{view.title}
              </p>
              <p
                className="mt-1 text-xs text-[#5f4965]"
                title="观察依据与行为分类是不同维度，多次模型观察不等于准确率。"
              >
                观察依据：{observationBasis(item)} · {view.source}
              </p>
              <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-[#992777]">
                <Clock size={12} />
                <span>
                  {identityLabels.length
                    ? personLabel(identityLabels, view.trackId, view.localStart)
                    : trackLabel(view.trackId)}{" "}
                  · {fmt(view.localStart)}—{fmt(view.localEnd)}
                </span>
              </p>
              {runId ? (
                <div className="mt-2.5">
                  <Link
                    to={`/qa?run=${encodeURIComponent(runId)}&track=${view.trackId}&start=${view.localStart}&end=${view.localEnd}`}
                    className={btnClass("secondary", "sm", "w-full")}
                  >
                    <MessageSquareText size={13} />
                    就这段证据提问
                  </Link>
                </div>
              ) : null}
            </div>

            <p className="text-xs leading-relaxed text-[#6e5a74]">
              {view.source === "教师确认"
                ? "以下为教师独立复核内容，仅适用于所填区间。"
                : "以下直接展示推断动作，可点击证据回放；原始校验结果见详情。"}
            </p>

            <div className="space-y-1">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-[#6e5a74]">
                {view.source === "教师确认"? "教师观察": "AI画面描述"}
              </h3>
              <p className="rounded-lg bg-[#f7f1f8] px-3 py-2 text-sm leading-relaxed text-[#231426]">
                {view.description || "此片段尚未获得具体动作描述，请直接查看视频。"}
              </p>
            </div>

            {(item.state === "rejected"||
              item.humanCorrection?.status === "rejected") && (
              <p
                role="note"
                className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800"
              >
                这条候选已有反证或已被教师驳回，不能作为支持该行为的事实。
              </p>
            )}

            {item.humanCorrection?.status === "confirmed"&&
              item.humanCorrection.track_id !== item.trackId && (
                <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                  教师修正了执行者。下方保留原模型针对{trackLabel(item.trackId)}
                  的输入帧，原取证对象不会随人工修改被改写。
                </p>
              )}

            {item.frames.length > 0 && (
              <div className="grid grid-cols-2 gap-2">
                {item.frames.map((f) => (
                  <button
                    type="button"
                    key={f.frame_id}
                    onClick={() => onSeek(f.local_time)}
                    aria-label={`证据帧 ${f.local_time.toFixed(3)}秒`}
                    className="group overflow-hidden rounded-lg border border-[#e3d2e6] bg-[#130c17] text-left transition hover:border-[#992777] hover:ring-2 hover:ring-[#992777]/20"
                  >
                    <div className="aspect-video w-full overflow-hidden bg-[#130c17]">
                      <img
                        loading="lazy"
                        src={f.url}
                        alt={`模型输入帧，局部${f.local_time.toFixed(3)}秒`}
                        className="h-full w-full object-cover transition duration-200 group-hover:scale-105"
                        onError={() =>
                          setFailed(
                            "证据帧加载失败：引用可能失效或登录过期，请重新读取。",
                          )
                        }
                      />
                    </div>
                    <span className="tabular block truncate bg-white px-2 py-1 text-[11px] font-medium text-[#4a3450]">
                      {fmt(f.local_time)} · Frame {f.frame_index}
                    </span>
                  </button>
                ))}
              </div>
            )}

            {failed ? <AlertBanner tone="danger">{failed}</AlertBanner> : null}

            <details className="rounded-lg border border-[#e8dce9] bg-[#faf7fb] px-3 py-2 text-xs">
              <summary className="flex cursor-pointer list-none items-center justify-between font-medium text-[#4a3450]">
                <span>分类依据、人工状态与证据限制</span>
                <ChevronDown size={14} className="opacity-60"/>
              </summary>
              <div className="mt-2 space-y-1.5 text-[#5f4965]">
                <p>
                  原类别证据等级提示：{view.reliability}。{view.reliabilityReason}
                </p>
                <p>
                  模型建议类别：{view.suggestion}；判定说明：
                  {stateLabel[item.state] || item.state}。{view.reliabilityReason}
                </p>
                <p>
                  人工状态：{humanReviewLabel[item.reviewStatus] || item.reviewStatus}
                </p>
                <p className="text-[11px] opacity-80">
                  图像包含完整场景与目标ROI。蓝色TARGET仅标记待核验人物，不证明连续身份一致。以上范围是抽样证据范围，不能当作动作精确边界。
                </p>
                {item.limitations.length > 0 && (
                  <ul className="list-disc space-y-1 pl-4">
                    {item.limitations.map((l, i) => (
                      <li key={i}>{l}</li>
                    ))}
                  </ul>
                )}
              </div>
            </details>

            <details className="rounded-lg border border-[#e8dce9] bg-[#faf7fb] px-3 py-2 text-xs">
              <summary className="flex cursor-pointer list-none items-center justify-between font-medium text-[#4a3450]">
                <span>原始状态、主体检查与CESA详情</span>
                <ChevronDown size={14} className="opacity-60"/>
              </summary>
              <pre className="mt-2 max-h-56 overflow-auto rounded bg-[#130c17] p-2.5 font-mono text-[11px] text-[#e9dced]">
                {JSON.stringify(item.raw, null, 2)}
              </pre>
            </details>
          </>
        )}
      </div>
    </section>
  );
}

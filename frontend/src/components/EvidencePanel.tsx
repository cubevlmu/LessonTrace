import { useState } from "react";
import {personLabel,type IdentityLabel} from '../api/workbench';
import {
  fmt,
  stateLabel,
  humanReviewLabel,
  trackLabel,
  observationView,
  observationBasis,
  type VisualItem,
} from "../models/view";
export function StatusChip({ status }: { status: string }) {
  return (
    <span className={"badge badge--" + status} title={status}>
      {stateLabel[status] || status}
    </span>
  );
}
export function EvidencePanel({
  item,
  onSeek,
  identityLabels=[],
}: {
  item?: VisualItem;
  onSeek: (t: number) => void;
  identityLabels?:IdentityLabel[];
}) {
  const [failed, setFailed] = useState("");
  const view=item?observationView(item):null;
  return (
    <section className="card evidence-panel">
      <div className="card__head">
        <h2 className="card__title">证据面板</h2>
        {view && <span className="badge badge--observation" title={item?.state}>{view.source}</span>}
      </div>
      <div className="card__body">
        {!item ? (
          <p className="muted">
            请选择观察窗口。没有可靠类别时仍展示实际视觉观察。
          </p>
        ) : (
          <>
            <p className="evidence__title">
              识别状态：{view!.title}
            </p>
            <p className="runtime-hint" title="观察依据与行为分类是不同维度，多次模型观察不等于准确率。">观察依据：{observationBasis(item)} · {view!.source}</p>
            <p>
              {identityLabels.length?personLabel(identityLabels,view!.trackId,view!.localStart):trackLabel(view!.trackId)} · {fmt(view!.localStart)}—
              {fmt(view!.localEnd)}
            </p>
            <p className="evidence__source">{view!.source==='教师确认'?'以下为教师独立复核内容，仅适用于所填区间。':'以下直接展示AI近似识别的动作，可点击证据回放；原始校验结果见详情。'}</p>
            <h3 className="card__title">{view!.source==='教师确认'?'教师观察':'AI画面描述'}</h3>
            <p className="evidence__observation">{view!.description||'此片段尚未获得具体动作描述，请直接查看视频。'}</p>
            {(item.state==='rejected'||item.humanCorrection?.status==='rejected')&&<p role="note" className="runtime-hint">这条候选已有反证或已被教师驳回，不能作为支持该行为的事实。</p>}
            {item.humanCorrection?.status==='confirmed'&&item.humanCorrection.track_id!==item.trackId&&<p className="evidence__source">教师修正了执行者。下方保留原模型针对{trackLabel(item.trackId)}的输入帧，原取证对象不会随人工修改被改写。</p>}
            <div className="product-frames">
              {item.frames.map((f) => (
                <button
                  type="button"
                  key={f.frame_id}
                  className="product-frame"
                  onClick={() => onSeek(f.local_time)}
                  aria-label={`证据帧 ${f.local_time.toFixed(3)}秒`}
                >
                  <img
                    loading="lazy"
                    src={f.url}
                    alt={`模型输入帧，局部${f.local_time.toFixed(3)}秒`}
                    onError={() =>
                      setFailed(
                        "证据帧加载失败：引用可能失效或登录过期，请重新读取。",
                      )
                    }
                  />
                  <span>
                    {fmt(f.local_time)} · Frame {f.frame_index}
                  </span>
                </button>
              ))}
            </div>
            {failed && <p role="alert">{failed}</p>}
            <details><summary>分类依据、人工状态与证据限制</summary>
            <p>原类别证据等级提示：{view!.reliability}。{view!.reliabilityReason}</p>
            <p>模型建议类别：{view!.suggestion}；判定说明：{stateLabel[item.state]||item.state}。{view!.reliabilityReason}</p>
            <p>人工状态：{humanReviewLabel[item.reviewStatus]||item.reviewStatus}</p>
            <p className="faint">
              图像包含完整场景与目标ROI。蓝色TARGET仅标记待核验人物，不证明连续身份一致。以上范围是抽样证据范围，不能当作动作精确边界。
            </p>
            {item.limitations.length > 0 && (
              <ul>
                {item.limitations.map((l, i) => (
                  <li className="muted" key={i}>
                    {l}
                  </li>
                ))}
              </ul>
            )}
            </details>
            <details>
              <summary>原始状态、主体检查与CESA详情</summary>
              <pre>{JSON.stringify(item.raw, null, 2)}</pre>
            </details>
          </>
        )}
      </div>
    </section>
  );
}

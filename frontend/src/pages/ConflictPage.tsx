import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useSession } from "../state/session";
import { visualItems } from "../adapters/event";
import { CorrectionPanel } from "../components/CorrectionPanel";
import { EvidencePanel } from "../components/EvidencePanel";
import { btnClass, selectClass } from "../components/ui/primitives";

export function ConflictPage() {
  const s = useSession();
  const items = useMemo(
    () => visualItems(s.data.events, s.data.windows),
    [s.data.events, s.data.windows],
  );
  const [id, setId] = useState("");
  const item = items.find((x) => x.id === id) || items[0];

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900">
            教师确认与纠错
          </h1>
          <p className="mt-0.5 text-xs text-slate-500">
            AI观察、AI建议和教师确认分别保存。修改可撤销，影响报告时需重算。
          </p>
        </div>
        <Link to="/review"className={btnClass("secondary", "sm")}>
          <ArrowLeft size={14} />
          返回视频工作台看片
        </Link>
      </header>

      <select
        className={`${selectClass} max-w-md`}
        aria-label="选择纠错事件"
        value={item?.id || ""}
        onChange={(e) => setId(e.target.value)}
      >
        <option value="">选择事件</option>
        {items.map((i) => (
          <option key={i.id} value={i.id}>
            Track {i.trackId} · {i.localStart.toFixed(2)}秒 · {i.category}
          </option>
        ))}
      </select>

      {!items.length && (
        <p className="text-xs text-slate-500">
          暂无已保存观察，请先完成视频分析。
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <CorrectionPanel item={item} />
        <EvidencePanel
          item={item}
          onSeek={(t) => {
            window.location.href = `/app/review?t=${t}`;
          }}
        />
      </div>
    </div>
  );
}

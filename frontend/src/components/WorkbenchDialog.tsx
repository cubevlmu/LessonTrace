import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { btnClass } from "./ui/primitives";

export function WorkbenchDialog({
  title,
  subtitle,
  children,
  onClose,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => d?.close();
  }, []);

  return (
    <dialog
      ref={ref}
      className="lt-dialog"
      aria-label={title}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
        <div>
          <h2 className="text-base font-semibold text-slate-900">
            {title}
          </h2>
          {subtitle ? (
            <p className="mt-0.5 text-xs text-slate-500">
              {subtitle}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          className={btnClass("ghost", "sm")}
          onClick={onClose}
        >
          <X size={14} />
          关闭
        </button>
      </header>
      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {children}
      </div>
    </dialog>
  );
}

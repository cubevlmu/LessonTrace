import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";
import { cn } from "./primitives";

type ToastTone = "success"| "error"| "info";
interface ToastItem {
  id: number;
  tone: ToastTone;
  text: string;
}

const ToastContext = createContext<{
  push: (text: string, tone?: ToastTone) => void;
}>({ push: () => {} });

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: number) => {
    setItems((list) => list.filter((i) => i.id !== id));
  }, []);

  const push = useCallback(
    (text: string, tone: ToastTone = "info") => {
      const id = nextId++;
      setItems((list) => [...list.slice(-3), { id, tone, text }]);
      setTimeout(() => dismiss(id), 4200);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed right-4 bottom-4 z-50 flex max-w-sm flex-col gap-2 no-print"
      >
        {items.map((item) => {
          const IconComp =
            item.tone === "success"
              ? CheckCircle2
              : item.tone === "error"
                ? AlertCircle
                : Info;
          return (
            <div
              key={item.id}
              className={cn(
                "pointer-events-auto flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm shadow-lg backdrop-blur-md",
                {
                  "border-emerald-200 bg-white/95 text-emerald-900":
                    item.tone === "success",
                  "border-rose-200 bg-white/95 text-rose-900":
                    item.tone === "error",
                  "border-slate-200 bg-white/95 text-slate-800":
                    item.tone === "info",
                },
              )}
            >
              <IconComp size={16} className="mt-0.5 shrink-0"/>
              <span className="flex-1 leading-snug">{item.text}</span>
              <button
                type="button"
                aria-label="关闭提示"
                onClick={() => dismiss(item.id)}
                className="rounded p-0.5 opacity-60 hover:opacity-100"
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

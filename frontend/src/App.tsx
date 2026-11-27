import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation, Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { SessionProvider, useSession } from "./state/session";
import { ToastProvider } from "./components/ui/Toast";
import { AppShell } from "./components/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { ReviewPage } from "./pages/ReviewPage";
import { ChatPage } from "./pages/ChatPage";
import { ReportPage } from "./pages/ReportPage";
import { ClassroomsPage } from "./pages/ClassroomsPage";
import { AccountPage, ModelConfigPage } from "./pages/AccountPage";
import { MaterialsPage } from "./pages/MaterialsPage";

const Demo = __PRODUCT__ ? null : lazy(() => import("./demo/App"));

function Product() {
  const { account, checking } = useSession();
  if (checking) {
    return (
      <div
        role="status"
        className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[var(--lt-bg)] text-sm text-[#5f4965]"
      >
        <Loader2 size={24} className="animate-spin text-[#992777]" />
        <p>正在恢复登录和服务器任务……</p>
      </div>
    );
  }
  if (!account) return <LoginPage />;
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route element={<AppShell />}>
        <Route path="account" element={<AccountPage />} />
        <Route path="account/model" element={<ModelConfigPage />} />
        <Route path="materials" element={<MaterialsPage />} />
        <Route path="classrooms" element={<ClassroomsPage />} />
        <Route path="review" element={<ReviewPage />} />
        <Route path="qa" element={<ChatPage />} />
        <Route path="conflict" element={<Navigate to="/review" replace />} />
        <Route path="report" element={<ReportPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  const location = useLocation();
  if (!__PRODUCT__ && Demo && location.pathname.startsWith("/demo")) {
    return (
      <Suspense
        fallback={
          <p className="p-6 text-sm text-[#6e5a74]">正在打开独立演示……</p>
        }
      >
        <div
          className="flex items-center justify-between gap-3 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900"
          role="note"
        >
          <span>
            独立演示模式：全部事件、问答和复核为组员提供的模拟数据，不会写入真实后端。
          </span>
          <Link to="/" className="font-semibold underline">
            返回正式应用
          </Link>
        </div>
        <Demo />
      </Suspense>
    );
  }
  return (
    <ToastProvider>
      <SessionProvider>
        <Product />
      </SessionProvider>
    </ToastProvider>
  );
}

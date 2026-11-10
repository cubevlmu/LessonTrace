import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation, Link } from "react-router-dom";
import { SessionProvider, useSession } from "./state/session";
import { AppShell } from "./components/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { ReviewPage } from "./pages/ReviewPage";
import { ChatPage } from "./pages/ChatPage";
import { ReportPage } from "./pages/ReportPage";
import { ClassroomsPage } from "./pages/ClassroomsPage";
import {AccountPage} from './pages/AccountPage';
import {MaterialsPage} from './pages/MaterialsPage';
const Demo = __PRODUCT__?null:lazy(() => import("./demo/App"));
function Product() {
  const { account, checking } = useSession();
  if (checking)
    return (
      <p className="upload__main" role="status">
        正在恢复登录和服务器任务……
      </p>
    );
  if (!account) return <LoginPage />;
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/review" replace state={{chooseLesson:true}} />} />
      <Route element={<AppShell />}>
        <Route path="account" element={<AccountPage />} />
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
  if (!__PRODUCT__ && Demo && location.pathname.startsWith("/demo"))
    return (
      <Suspense fallback={<p>正在打开独立演示……</p>}>
        <div className="src-bar demo-mode-banner" role="note">
          独立演示模式：全部事件、问答和复核为组员提供的模拟数据，不会写入真实后端。
          <Link to="/">返回正式应用</Link>
        </div>
        <Demo />
      </Suspense>
    );
  return (
    <SessionProvider>
      <Product />
    </SessionProvider>
  );
}

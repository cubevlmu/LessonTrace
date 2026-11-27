import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Users,
  FolderOpen,
  Clapperboard,
  MessageSquareText,
  FileText,
  UserCog,
  Cpu,
  Video,
  ChevronDown,
  LogOut,
  Menu,
  X,
  RefreshCw,
} from "lucide-react";
import { useSession } from "../state/session";
import { videoTitle } from "../models/view";
import { AlertBanner, btnClass, cn } from "./ui/primitives";

const workspaceNav = [
  { to: "/review", label: "工作台", icon: Clapperboard },
  { to: "/qa", label: "课堂助手", icon: MessageSquareText },
  { to: "/report", label: "复盘报告", icon: FileText },
  { to: "/classrooms", label: "班级管理", icon: Users },
  { to: "/materials", label: "教学资料", icon: FolderOpen },
];

const settingsNav = [
  { to: "/account", label: "账户管理", icon: UserCog, end: true },
  { to: "/account/model", label: "模型配置", icon: Cpu, end: false },
];

const roleNames: Record<string, string> = {
  admin: "管理员",
  teacher: "教师",
  reviewer: "复核员",
};

export function AppShell() {
  const s = useSession();
  const location = useLocation();
  const nav = useNavigate();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const video = s.data.videos.find((v) => v.video_id === s.data.videoId);

  return (
    <div className="min-h-screen bg-[var(--lt-bg)] text-[var(--lt-ink)]">
      {/* Top Bar */}
      <header className="lt-topbar sticky top-0 z-30 flex h-12 items-center gap-3 border-b border-[#e8dce9] bg-white/95 px-3.5 backdrop-blur-md">
        <button
          type="button"
          aria-label="切换导航菜单"
          onClick={() => setMobileNavOpen((v) => !v)}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[#5f4965] hover:bg-[#f7eaf3] lg:hidden"
        >
          {mobileNavOpen ? <X size={17} /> : <Menu size={17} />}
        </button>

        {/* Brand */}
        <div className="flex shrink-0 items-center lg:w-40">
          <span className="text-[15px] font-bold tracking-tight text-[#992777]">
            课迹 LessonTrace
          </span>
        </div>

        {/* Lesson Switcher Pill -> navigates to unified video selector */}
        <button
          type="button"
          onClick={() => nav("/")}
          className="inline-flex max-w-[220px] sm:max-w-xs items-center gap-1.5 rounded-full border border-[#e3bed8] bg-[#faf2f8] px-3 py-1 text-xs font-medium text-[#6e1a55] transition hover:border-[#992777] hover:bg-[#f5e3f0]"
        >
          <Video size={13} className="shrink-0 text-[#992777]" />
          <span className="truncate" title={video?.metadata.filename}>
            {video ? videoTitle(video) : "选择课堂录像"}
          </span>
          <ChevronDown size={12} className="shrink-0 opacity-65" />
        </button>

        <div className="flex-1" />

        {/* Logout */}
        <button
          type="button"
          className={btnClass("ghost", "sm")}
          onClick={() => void s.logout().catch((e) => s.setError(String(e)))}
        >
          <LogOut size={13} />
          <span className="hidden sm:inline">退出</span>
        </button>
      </header>

      <div className="flex min-h-[calc(100vh-3rem)]">
        {/* Compact PrimeUI-inspired Left Sidebar */}
        <nav
          aria-label="主导航"
          className={cn(
            "lt-sidebar fixed inset-y-12 left-0 z-20 flex w-44 shrink-0 flex-col justify-between border-r border-[#e8dce9] bg-white px-2 py-2.5 transition-transform duration-200 lg:sticky lg:top-12 lg:h-[calc(100vh-3rem)] lg:translate-x-0",
            mobileNavOpen ? "translate-x-0 shadow-xl" : "-translate-x-full",
          )}
        >
          <div className="space-y-3 overflow-y-auto">
            <div className="space-y-0.5">
              <p className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[#9c89a2]">
                课堂工作台
              </p>
              {workspaceNav.map((item) => {
                const IconComp = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    onClick={() => setMobileNavOpen(false)}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors",
                        isActive
                          ? "bg-[#f7eaf3] font-semibold text-[#992777]"
                          : "text-[#54405a] hover:bg-[#faf4fb] hover:text-[#231426]",
                      )
                    }
                  >
                    <IconComp size={15} className="shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </NavLink>
                );
              })}
            </div>

            <div className="border-t border-[#f2e8f4] pt-2.5 space-y-0.5">
              <p className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[#9c89a2]">
                系统设置
              </p>
              {settingsNav.map((item) => {
                const IconComp = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={() => setMobileNavOpen(false)}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors",
                        isActive
                          ? "bg-[#f7eaf3] font-semibold text-[#992777]"
                          : "text-[#54405a] hover:bg-[#faf4fb] hover:text-[#231426]",
                      )
                    }
                  >
                    <IconComp size={15} className="shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          </div>

          <div className="flex items-center justify-between gap-1.5 rounded-lg border border-[#efe4f1] bg-[#faf7fb] px-2.5 py-1.5 text-[11px]">
            <span className="truncate font-semibold text-[#231426]">
              {s.account?.username || "教师"}
            </span>
            <span className="shrink-0 text-[#7d6984]">
              {roleNames[s.account?.role || ""] || "只读"}
            </span>
          </div>
        </nav>

        {/* Mobile sidebar backdrop */}
        {mobileNavOpen ? (
          <div
            className="fixed inset-0 top-12 z-10 bg-[#231426]/35 backdrop-blur-xs lg:hidden"
            onClick={() => setMobileNavOpen(false)}
          />
        ) : null}

        {/* Main Content with Page Transition */}
        <main className="min-w-0 flex-1 px-4 py-4 sm:px-6">
          {s.error ? (
            <AlertBanner
              tone="danger"
              className="mb-4"
              action={
                <button
                  type="button"
                  className={btnClass("ghost", "sm")}
                  onClick={() => void s.refresh()}
                >
                  <RefreshCw size={13} />
                  重新读取
                </button>
              }
            >
              {s.error}
            </AlertBanner>
          ) : null}
          <div key={location.pathname} className="lt-page-enter">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

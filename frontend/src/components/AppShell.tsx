import { NavLink, Outlet } from "react-router-dom";
import {LessonChooser} from './LessonChooser';
import { Icon, type IconName } from "./Icon";
import { ThemeToggle } from "./ThemeToggle";
import { useSession } from "../state/session";
import {videoTitle} from '../models/view';
const nav: { to: string; label: string; icon: IconName }[] = [
  { to: "/classrooms", label: "班级管理", icon: "user" },
  { to: "/materials", label: "教学资料", icon: "report" },
  { to: "/review", label: "复盘工作台", icon: "timeline" },
  { to: "/qa", label: "课堂问答", icon: "chat" },
  { to: "/report", label: "复盘报告", icon: "report" },
  { to: "/account", label: "设置", icon: "shield" },
];
export function AppShell() {
  const s = useSession();
  const video = s.data.videos.find((v) => v.video_id === s.data.videoId);
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand__mark">
            <Icon name="trace" size={17} />
          </div>
          <div className="brand__name">课迹 LessonTrace</div>
        </div>
        <button className="session-pill" onClick={()=>window.dispatchEvent(new Event('lessontrace:choose-lesson'))}>
          <Icon name="camera" size={15} />
          <span title={video?.metadata.filename}>{video?videoTitle(video):'选择课堂录像'}</span>
          <Icon name="chevronDown" size={14} />
        </button>
        <div className="topbar__spacer" />
        <div className="topbar__meta">
          <span className="anon-chip">
            <Icon name="shield" size={13} />
            证据可追溯 · 结果可复核
          </span>
          <ThemeToggle />
          <button
            className="btn btn--ghost btn--sm"
            onClick={() => void s.logout().catch((e) => s.setError(String(e)))}
          >
            退出
          </button>
        </div>
      </header>
      <nav className="rail" aria-label="主导航">
        <div className="rail__group">
          {nav.map((n) => (
            <NavLink className="navitem" to={n.to} key={n.to}>
              <Icon name={n.icon} size={17} className="navitem__icon" />
              <span className="navitem__text">{n.label}</span>
            </NavLink>
          ))}
        </div>
        <div className="rail__foot">
          <div className="rail-note rail-note--tight">
            {s.account?.username || '教师工作空间'}
            <br />
            {({admin:'管理员',teacher:'教师',reviewer:'复核员'} as Record<string,string>)[s.account?.role||'']||'只读账户'}
          </div>
        </div>
      </nav>
      <main className="main">
        {s.error && (
          <p className="upload__error" role="alert">
            {s.error}
            <button className="btn btn--ghost" onClick={() => void s.refresh()}>
              重新读取
            </button>
          </p>
        )}
        <Outlet />
        <LessonChooser/>
      </main>
    </div>
  );
}

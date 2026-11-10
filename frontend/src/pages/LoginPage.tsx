import { useState } from "react";
import { Link } from "react-router-dom";
import { ThemeToggle } from "../components/ThemeToggle";
import { Icon } from "../components/Icon";
import { useSession } from "../state/session";
export function LoginPage() {
  const s = useSession();
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <div className="upload">
      <header className="upload__top">
        <div className="brand">
          <div className="brand__mark">
            <Icon name="trace" />
          </div>
          <div className="brand__name">课迹 LessonTrace</div>
        </div>
        <ThemeToggle />
      </header>
      <main className="upload__main">
        <div className="upload__intro">
          <h1 className="upload__title">从课堂录像，回到可核查的证据</h1>
          <p className="upload__sub">
            登录本机工作空间，上传录像、查看匿名人物与行为观察，并生成带引用的课堂复盘。
          </p>
        </div>
        <form
          className="card card__body"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              await s.login(username, password);
              setUsername("");
              setPassword("");
            } catch (err) {
              setError(String(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <h2 className="card__title">登录工作空间</h2>
          <p className="muted">
            使用教师账户登录。密码发送到当前服务端，不保存到浏览器本地存储。
          </p>
          <label>
            用户名
            <input
              className="product-input"
              aria-label="用户名"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>
            密码
            <input
              className="product-input"
              aria-label="密码"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button className="btn btn--primary" disabled={busy || !username.trim() || !password}>
            {busy ? "正在登录……" : "登录"}
          </button>
          {(error || s.error) && (
            <p role="alert" className="upload__error">
              {error || s.error}
            </p>
          )}
        </form>
        {!__PRODUCT__ && <p className="upload__note">
          <Link to="/demo">查看独立演示（模拟数据）</Link>
        </p>}
      </main>
    </div>
  );
}

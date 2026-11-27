import { useState } from "react";
import { z } from "zod";
import { KeyRound, UserPlus } from "lucide-react";
import { request } from "../api/client";
import { useSession } from "../state/session";
import { ModelSettings } from "../components/ModelSettings";
import {
  AlertBanner,
  btnClass,
  cardClass,
  cardHeadClass,
  cardTitleClass,
  fieldClass,
  labelClass,
  selectClass,
} from "../components/ui/primitives";

export function AccountPage() {
  const s = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("teacher");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function perform(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight text-[#231426] sm:text-2xl">
          账户管理
        </h1>
        <p className="mt-0.5 text-xs text-[#6e5a74]">
          当前登录：{s.account?.username || s.account?.actor || "教师"}
        </p>
      </header>

      {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
      {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}

      <section className={cardClass}>
        <div className={cardHeadClass}>
          <h2 className={cardTitleClass}>
            <KeyRound size={15} className="text-[#992777]" />
            修改密码
          </h2>
        </div>
        <form
          className="grid gap-3 p-4 sm:grid-cols-3 sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void perform(async () => {
              await request("password", z.object({ changed: z.boolean() }), {
                current_password: current,
                new_password: next,
              });
              setCurrent("");
              setNext("");
              setMessage("密码已修改，其他登录会话已退出");
            });
          }}
        >
          <label className={labelClass}>
            当前密码
            <input
              className={fieldClass}
              aria-label="当前密码"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
            />
          </label>
          <label className={labelClass}>
            新密码
            <input
              className={fieldClass}
              aria-label="新密码"
              type="password"
              minLength={10}
              maxLength={256}
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
            />
          </label>
          <button
            type="submit"
            className={btnClass("primary", "md")}
            disabled={busy}
          >
            修改密码
          </button>
        </form>
      </section>

      {s.account?.role === "admin" && (
        <section className={cardClass}>
          <div className={cardHeadClass}>
            <h2 className={cardTitleClass}>
              <UserPlus size={15} className="text-[#992777]" />
              创建教师账户
            </h2>
          </div>
          <form
            className="grid gap-3 p-4 sm:grid-cols-4 sm:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              void perform(async () => {
                await request(
                  "users",
                  z.object({
                    actor: z.string(),
                    username: z.string(),
                    role: z.string(),
                  }),
                  { username, password, role },
                );
                setUsername("");
                setPassword("");
                setMessage("账户已创建，请私下向教师提供用户名与初始密码");
              });
            }}
          >
            <label className={labelClass}>
              用户名
              <input
                className={fieldClass}
                aria-label="新账户用户名"
                autoComplete="off"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label className={labelClass}>
              初始密码
              <input
                className={fieldClass}
                aria-label="初始密码"
                autoComplete="new-password"
                type="password"
                minLength={10}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <label className={labelClass}>
              角色
              <select
                className={selectClass}
                aria-label="账户角色"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                <option value="teacher">教师</option>
                <option value="reviewer">审核人</option>
                <option value="admin">管理员</option>
              </select>
            </label>
            <button
              type="submit"
              className={btnClass("primary", "md")}
              disabled={busy}
            >
              创建账户
            </button>
          </form>
        </section>
      )}
    </div>
  );
}

export function ModelConfigPage() {
  const s = useSession();
  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight text-[#231426] sm:text-2xl">
          模型配置
        </h1>
        <p className="mt-0.5 text-xs text-[#6e5a74]">
          配置课堂助手与复盘报告使用的语言模型 ·{" "}
          {s.account?.username || s.account?.actor || "教师"}
        </p>
      </header>
      <ModelSettings />
    </div>
  );
}

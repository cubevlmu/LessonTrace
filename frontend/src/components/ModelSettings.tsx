import { useEffect, useState } from "react";
import { z } from "zod";
import { Cpu } from "lucide-react";
import { request } from "../api/client";
import { useSession } from "../state/session";
import {
  AlertBanner,
  badgeClass,
  btnClass,
  cardClass,
  cardHeadClass,
  cardTitleClass,
  fieldClass,
  labelClass,
  selectClass,
} from "./ui/primitives";

const schema = z.object({
  provider: z.string(),
  model: z.string(),
  base_url: z.string(),
  key_configured: z.boolean(),
  timeout: z.number(),
  context_chars: z.number(),
  max_tokens: z.number(),
  allow_text_transfer: z.boolean(),
  configuration_source: z.string(),
});

type Config = z.infer<typeof schema>;

export function ModelSettings() {
  const s = useSession();
  const [config, setConfig] = useState<Config | null>(null);
  const [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    void request("model-settings", schema)
      .then(setConfig)
      .catch((e) => setError(String(e)));
  }, []);

  async function save() {
    if (!config) return;
    setBusy(true);
    setError("");
    try {
      setConfig(
        await request(
          "model-settings",
          schema,
          {
            provider: config.provider,
            base_url: config.base_url,
            model: config.model,
            timeout: config.timeout,
            context_chars: config.context_chars,
            max_tokens: config.max_tokens,
            allow_text_transfer: config.allow_text_transfer,
            api_key: key || null,
            clear_key: clearKey,
          },
          "PUT",
        ),
      );
      setKey("");
      setClearKey(false);
      setMessage("模型设置已加密保存，下次问答生效。旧报告将检查配置版本。");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={cardClass}>
      <div className={cardHeadClass}>
        <h2 className={cardTitleClass}>
          <Cpu size={15} className="text-[#992777]"/>
          问答与报告模型
        </h2>
        <span className={badgeClass("neutral")}>
          {config?.provider === "ollama"
            ? "本机调试 · 千问小模型"
            : "生产接入 · OpenAI兼容API"}
        </span>
      </div>

      <div className="space-y-3.5 p-4">
        {error ? <AlertBanner tone="danger">{error}</AlertBanner> : null}
        {message ? <AlertBanner tone="status">{message}</AlertBanner> : null}

        {config ? (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <fieldset
              disabled={s.account?.role !== "admin"|| busy}
              className="space-y-3.5 border-0 p-0 m-0"
            >
              <label className={labelClass}>
                运行方式
                <select
                  className={selectClass}
                  aria-label="文本模型运行方式"
                  value={config.provider}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      provider: e.target.value,
                      base_url:
                        e.target.value === "ollama"
                          ? "http://127.0.0.1:11434"
                          : "https://api.deepseek.com/v1",
                      model:
                        e.target.value === "ollama"
                          ? "qwen3:4b-instruct"
                          : "deepseek-chat",
                      allow_text_transfer: false,
                    })
                  }
                >
                  <option value="ollama">本地Ollama（开发调试）</option>
                  <option value="openai_compatible">
                    OpenAI兼容API（生产环境）
                  </option>
                </select>
              </label>

              <div className="grid gap-3 sm:grid-cols-2">
                <label className={labelClass}>
                  服务地址
                  <input
                    className={fieldClass}
                    aria-label="模型服务地址"
                    required
                    value={config.base_url}
                    onChange={(e) =>
                      setConfig({ ...config, base_url: e.target.value })
                    }
                  />
                </label>

                <label className={labelClass}>
                  模型名称
                  <input
                    className={fieldClass}
                    aria-label="文本模型名称"
                    required
                    value={config.model}
                    onChange={(e) =>
                      setConfig({ ...config, model: e.target.value })
                    }
                  />
                </label>
              </div>

              {config.provider === "openai_compatible"&& (
                <div className="space-y-2.5 rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5">
                  <label className={labelClass}>
                    API密钥
                    <input
                      className={fieldClass}
                      aria-label="模型API密钥"
                      type="password"
                      autoComplete="new-password"
                      value={key}
                      placeholder={
                        config.key_configured
                          ? "已保存；留空保留原密钥"
                          : "仅保存在服务端"
                      }
                      onChange={(e) => setKey(e.target.value)}
                    />
                  </label>
                  <label className="flex items-start gap-2 text-xs text-slate-700">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={config.allow_text_transfer}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          allow_text_transfer: e.target.checked,
                        })
                      }
                    />
                    <span>
                      允许向该服务发送本次有权限的课堂文字资料与观察摘要；不发送照片、视频或生物特征。
                    </span>
                  </label>
                  <label className="flex items-center gap-2 text-xs text-slate-700">
                    <input
                      type="checkbox"
                      checked={clearKey}
                      onChange={(e) => setClearKey(e.target.checked)}
                    />
                    清除已存密钥
                  </label>
                </div>
              )}

              <details className="rounded-lg border border-slate-200/80 p-3 text-xs">
                <summary className="cursor-pointer font-medium text-slate-600">
                  生成与上下文限制
                </summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  <label className={labelClass}>
                    超时秒数
                    <input
                      className={fieldClass}
                      type="number"
                      min="10"
                      max="180"
                      value={config.timeout}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          timeout: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label className={labelClass}>
                    背景文字预算
                    <input
                      className={fieldClass}
                      type="number"
                      min="4000"
                      max="120000"
                      value={config.context_chars}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          context_chars: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label className={labelClass}>
                    生成Token上限
                    <input
                      className={fieldClass}
                      type="number"
                      min="500"
                      max="6000"
                      value={config.max_tokens}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          max_tokens: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                </div>
              </details>

              {s.account?.role === "admin"&& (
                <button
                  type="submit"
                  className={btnClass("primary", "md")}
                  disabled={busy}
                >
                  保存模型设置
                </button>
              )}
            </fieldset>

            <p className="text-xs text-slate-500">
              本地模式不会自动下载模型。生产模式由管理员配置；密钥不回传浏览器，不会出现在导出报告中。视觉分析模型仍由独立运行配置管理。
            </p>
          </form>
        ) : (
          <p className="text-xs text-slate-500">正在读取配置…</p>
        )}
      </div>
    </section>
  );
}

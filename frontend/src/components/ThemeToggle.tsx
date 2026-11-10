import { Icon } from "./Icon";
import { toggleTheme, useTheme } from "../lib/theme";

/**
 * 日间 / 夜间切换。图标按钮，放在顶栏右侧；上传页也放一个。
 * 文案用 aria-label 讲清楚「切过去会变成什么」。
 */
export function ThemeToggle({ withLabel = false }: { withLabel?: boolean }) {
  const theme = useTheme();
  const dark = theme === "dark";
  const label = dark ? "切到日间模式" : "切到夜间模式";

  return (
    <button
      type="button"
      className={
        withLabel ? "theme-toggle theme-toggle--label" : "theme-toggle"
      }
      onClick={toggleTheme}
      aria-label={label}
      aria-pressed={dark}
      title={label}
    >
      <Icon name={dark ? "sun" : "moon"} size={16} />
      {withLabel ? <span>{dark ? "日间" : "夜间"}</span> : null}
    </button>
  );
}

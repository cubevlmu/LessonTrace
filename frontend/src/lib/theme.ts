import { useSyncExternalStore } from "react";

/**
 * 主题：light / dark。
 * 选择存在 localStorage，第一次进来跟随系统；切换只改 <html data-theme>，
 * 所有颜色都走 tokens.css 里的语义令牌，组件本身不感知主题。
 */
export type Theme = "light" | "dark";

const KEY = "lessontrace.theme";
const listeners = new Set<() => void>();

let current: Theme = "light";

function systemTheme(): Theme {
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function apply(): void {
  const root = document.documentElement;
  root.dataset.theme = current;
  root.style.colorScheme = current;
}

export function initTheme(): void {
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(KEY);
  } catch {
    /* 隐私模式 / 禁用存储：退回跟随系统 */
  }
  current = stored === "dark" || stored === "light" ? stored : systemTheme();
  apply();
}

export function getTheme(): Theme {
  return current;
}

export function setTheme(next: Theme): void {
  if (next === current) return;
  current = next;
  try {
    window.localStorage.setItem(KEY, next);
  } catch {
    /* 存不了就算了，本次会话仍然生效 */
  }
  apply();
  listeners.forEach((fn) => fn());
}

export function toggleTheme(): void {
  setTheme(current === "dark" ? "light" : "dark");
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getTheme, getTheme);
}

export type Theme = "light";

export function initTheme(): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = "light";
  document.documentElement.style.colorScheme = "light";
}

export function getTheme(): Theme {
  return "light";
}

export function setTheme(_next: Theme): void {
  initTheme();
}

export function toggleTheme(): void {
  initTheme();
}

export function useTheme(): Theme {
  return "light";
}

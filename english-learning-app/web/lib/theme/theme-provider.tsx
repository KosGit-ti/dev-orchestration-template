"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { readJson, storageKey, writeJson } from "../storage/kv";

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const THEME_KEY = storageKey("theme", "v1");

interface ThemeState {
  readonly preference: ThemePreference;
  /** 実際に適用されている配色。`system` のとき OS 設定で決まる。 */
  readonly resolved: ResolvedTheme;
  setPreference(preference: ThemePreference): void;
}

const ThemeContext = createContext<ThemeState | null>(null);

function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || !window.matchMedia) {
    return "light";
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
  // ブラウザ UI（iOS Safari のステータスバー等）も配色へ追従させる。
  const meta = document.querySelector('meta[name="theme-color"]');
  meta?.setAttribute("content", resolved === "dark" ? "#0b1220" : "#f6f8fb");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>("system");
  const [resolved, setResolved] = useState<ResolvedTheme>("light");

  // 保存済みの設定を復元する。SSR では window が無いので effect で行う。
  useEffect(() => {
    const stored = readJson<ThemePreference>(THEME_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") {
      setPreferenceState(stored);
    }
  }, []);

  // 設定と OS の状態から実効配色を決め、OS 側の変化にも追従する。
  useEffect(() => {
    const compute = () => {
      const next = preference === "system" ? systemTheme() : preference;
      setResolved(next);
      applyTheme(next);
    };
    compute();
    if (preference !== "system" || typeof window === "undefined" || !window.matchMedia) {
      return;
    }
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    query.addEventListener("change", compute);
    return () => query.removeEventListener("change", compute);
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    writeJson(THEME_KEY, next);
  }, []);

  const value = useMemo<ThemeState>(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme は ThemeProvider の内側でだけ使えます。");
  }
  return context;
}

/**
 * 初回描画で配色がちらつく（light で描いてから dark へ切り替わる）のを防ぐ
 * ため、body 描画前に同期実行するスクリプト。localStorage の値を直接読む。
 */
export const THEME_BOOTSTRAP_SCRIPT = `
(function () {
  try {
    var raw = localStorage.getItem(${JSON.stringify(THEME_KEY)});
    var pref = raw ? JSON.parse(raw) : "system";
    var dark = pref === "dark" ||
      (pref !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (dark) { document.documentElement.classList.add("dark"); }
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
  } catch (e) {}
})();
`;

"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme, type ThemePreference } from "@/lib/theme/theme-provider";
import { cn } from "@/lib/util/cn";

const OPTIONS: readonly { value: ThemePreference; label: string; Icon: typeof Sun }[] = [
  { value: "light", label: "ライト", Icon: Sun },
  { value: "system", label: "システム", Icon: Monitor },
  { value: "dark", label: "ダーク", Icon: Moon },
];

/** 配色切り替え。既定はシステム追従で、明示指定もできる 3 択にする。 */
export function ThemeToggle() {
  const { preference, setPreference } = useTheme();
  return (
    <div
      className="inline-flex rounded-xl border border-border bg-surface p-0.5"
      role="radiogroup"
      aria-label="配色"
    >
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={preference === value}
          aria-label={label}
          title={label}
          onClick={() => setPreference(value)}
          className={cn(
            "rounded-[0.625rem] p-2 transition-colors",
            preference === value
              ? "bg-accent text-accent-fg"
              : "text-muted hover:bg-surface-raised hover:text-fg",
          )}
        >
          <Icon className="h-4 w-4" aria-hidden />
        </button>
      ))}
    </div>
  );
}

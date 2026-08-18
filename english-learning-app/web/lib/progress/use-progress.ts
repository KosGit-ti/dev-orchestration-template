"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth/context";
import { emptyProgress, loadProgress, saveProgress } from "./store";
import type { Progress } from "./types";

/**
 * 進捗を React 側へ橋渡しする。
 *
 * 保存はここで一括して行う。各画面が直接 saveProgress を呼ぶと、保存漏れと
 * 二重保存の両方が起きるため、更新経路を update() 一本に絞る。
 */
export function useProgress(): {
  progress: Progress | null;
  update(mutate: (current: Progress, now: Date) => Progress): void;
  reload(): void;
} {
  const { user } = useAuth();
  const [progress, setProgress] = useState<Progress | null>(null);

  const reload = useCallback(() => {
    if (!user) {
      setProgress(null);
      return;
    }
    setProgress(loadProgress(user.id, new Date()));
  }, [user]);

  useEffect(() => {
    reload();
  }, [reload]);

  const update = useCallback(
    (mutate: (current: Progress, now: Date) => Progress) => {
      if (!user) {
        return;
      }
      const now = new Date();
      setProgress((current) => {
        const base = current ?? emptyProgress(user.id, now);
        const next = mutate(base, now);
        saveProgress(next);
        return next;
      });
    },
    [user],
  );

  return { progress, update, reload };
}

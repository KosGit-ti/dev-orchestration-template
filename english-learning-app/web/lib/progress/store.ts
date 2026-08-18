import { readJson, storageKey, writeJson } from "../storage/kv";
import { newCard, type Grade, type SrsCard, review as applyReview } from "../srs/sm2";
import {
  DEFAULT_DAILY_GOAL,
  MAX_DAILY_RECORDS,
  type DailyRecord,
  type PassageProgress,
  type PlacementResult,
  type Progress,
} from "./types";

const CURRENT_VERSION = 2 as const;

function keyFor(userId: string): string {
  return storageKey("progress", userId, "v2");
}

/** ローカル日付の `YYYY-MM-DD`。UTC ではなく端末時刻で切る（学習者の一日に合わせる）。 */
export function localDateKey(now: Date): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function emptyProgress(userId: string, now: Date): Progress {
  return {
    version: CURRENT_VERSION,
    userId,
    cards: {},
    passages: {},
    placement: null,
    dailyGoal: DEFAULT_DAILY_GOAL,
    daily: [],
    updatedAt: now.toISOString(),
  };
}

/** v1（daily を持たない初期形式）からの移行。未知の版は破棄せず空へ倒す。 */
export function migrate(raw: unknown, userId: string, now: Date): Progress {
  if (typeof raw !== "object" || raw === null) {
    return emptyProgress(userId, now);
  }
  const record = raw as Record<string, unknown>;
  const version = typeof record.version === "number" ? record.version : 0;
  if (version === CURRENT_VERSION) {
    return record as unknown as Progress;
  }
  if (version === 1) {
    return {
      ...emptyProgress(userId, now),
      cards: (record.cards as Progress["cards"]) ?? {},
      passages: (record.passages as Progress["passages"]) ?? {},
      placement: (record.placement as PlacementResult | null) ?? null,
      dailyGoal: typeof record.dailyGoal === "number" ? record.dailyGoal : DEFAULT_DAILY_GOAL,
    };
  }
  // 未知の版は解釈できない。学習記録を壊さないよう空から始め直す。
  return emptyProgress(userId, now);
}

export function loadProgress(userId: string, now: Date): Progress {
  return migrate(readJson<unknown>(keyFor(userId)), userId, now);
}

export function saveProgress(progress: Progress): boolean {
  return writeJson(keyFor(progress.userId), progress);
}

/** 単語 1 件の解答を反映する。 */
export function recordWordReview(
  progress: Progress,
  wordId: string,
  grade: Grade,
  now: Date,
  elapsedSeconds = 0,
): Progress {
  const current: SrsCard = progress.cards[wordId] ?? newCard(now);
  const updated = applyReview(current, grade, now);
  return {
    ...progress,
    cards: { ...progress.cards, [wordId]: updated },
    daily: bumpDaily(progress.daily, now, {
      wordsReviewed: 1,
      wordsCorrect: grade === "again" ? 0 : 1,
      passagesCompleted: 0,
      seconds: elapsedSeconds,
    }),
    updatedAt: now.toISOString(),
  };
}

/** パッセージ 1 本の完了を反映する。 */
export function recordPassage(
  progress: Progress,
  passageId: string,
  accuracy: number,
  now: Date,
  elapsedSeconds = 0,
): Progress {
  const previous = progress.passages[passageId];
  const entry: PassageProgress = {
    passageId,
    completedAt: now.toISOString(),
    // 再挑戦時は良いほうを残す。下振れした 1 回で到達記録を消さない。
    accuracy: Math.max(accuracy, previous?.accuracy ?? 0),
    attempts: (previous?.attempts ?? 0) + 1,
  };
  return {
    ...progress,
    passages: { ...progress.passages, [passageId]: entry },
    daily: bumpDaily(progress.daily, now, {
      wordsReviewed: 0,
      wordsCorrect: 0,
      passagesCompleted: 1,
      seconds: elapsedSeconds,
    }),
    updatedAt: now.toISOString(),
  };
}

export function recordPlacement(
  progress: Progress,
  placement: PlacementResult,
  now: Date,
): Progress {
  return { ...progress, placement, updatedAt: now.toISOString() };
}

export function setDailyGoal(progress: Progress, goal: number, now: Date): Progress {
  return {
    ...progress,
    dailyGoal: Math.max(5, Math.min(200, Math.round(goal))),
    updatedAt: now.toISOString(),
  };
}

type DailyDelta = Omit<DailyRecord, "date">;

function bumpDaily(
  daily: readonly DailyRecord[],
  now: Date,
  delta: DailyDelta,
): readonly DailyRecord[] {
  const date = localDateKey(now);
  const head = daily[0];
  if (head && head.date === date) {
    const merged: DailyRecord = {
      date,
      wordsReviewed: head.wordsReviewed + delta.wordsReviewed,
      wordsCorrect: head.wordsCorrect + delta.wordsCorrect,
      passagesCompleted: head.passagesCompleted + delta.passagesCompleted,
      seconds: head.seconds + delta.seconds,
    };
    return [merged, ...daily.slice(1)];
  }
  return [{ date, ...delta }, ...daily].slice(0, MAX_DAILY_RECORDS);
}

/**
 * 連続学習日数。今日か昨日に記録があれば継続中と見なす。
 * 今日まだ学習していなくても、昨日まで続いていれば連続を保つ。
 */
export function currentStreak(daily: readonly DailyRecord[], now: Date): number {
  if (daily.length === 0) {
    return 0;
  }
  const dates = new Set(
    daily
      .filter((record) => record.wordsReviewed + record.passagesCompleted > 0)
      .map((record) => record.date),
  );
  const cursor = new Date(now);
  if (!dates.has(localDateKey(cursor))) {
    cursor.setDate(cursor.getDate() - 1);
    if (!dates.has(localDateKey(cursor))) {
      return 0;
    }
  }
  let streak = 0;
  while (dates.has(localDateKey(cursor))) {
    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }
  return streak;
}

/** 今日の学習量。 */
export function todayRecord(daily: readonly DailyRecord[], now: Date): DailyRecord {
  const date = localDateKey(now);
  return (
    daily.find((record) => record.date === date) ?? {
      date,
      wordsReviewed: 0,
      wordsCorrect: 0,
      passagesCompleted: 0,
      seconds: 0,
    }
  );
}

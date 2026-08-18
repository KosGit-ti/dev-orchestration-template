import type { LevelId, WordEntry } from "../content/types";
import { wordsForLevel } from "../content/index";
import { isDue, newCard, type SrsCard } from "../srs/sm2";
import type { Progress } from "./types";
import { createRandom, shuffle } from "../util/shuffle";

export interface SessionPlan {
  /** 出題順に並んだ語。 */
  readonly items: readonly WordEntry[];
  /** 内訳。UI で「復習 12 / 新規 8」と示すために持つ。 */
  readonly dueCount: number;
  readonly newCount: number;
}

/**
 * 1 セッション分の出題を組み立てる。
 *
 * 方針は 2 つ。
 *   1. 期限が来た復習を新規より先に出す。復習が溜まったまま新規を足すと、
 *      どのレベルでも遅かれ早かれ破綻する。
 *   2. 復習だけで枠が埋まる日は新規を足さない。目標語数を守るために
 *      未学習語を押し込むと、復習の遅れが翌日以降へ積み上がる。
 */
export function planSession(
  progress: Progress,
  level: LevelId,
  limit: number,
  now: Date,
  seed: number,
): SessionPlan {
  const pool = wordsForLevel(level);
  const due: WordEntry[] = [];
  const fresh: WordEntry[] = [];

  for (const word of pool) {
    const card: SrsCard | undefined = progress.cards[word.id];
    if (!card) {
      fresh.push(word);
    } else if (isDue(card, now)) {
      due.push(word);
    }
  }

  // 期限超過が大きい順に出す。放置した語ほど忘却が進んでいる。
  const sortedDue = [...due].sort((a, b) => {
    const cardA = progress.cards[a.id] ?? newCard(now);
    const cardB = progress.cards[b.id] ?? newCard(now);
    return Date.parse(cardA.due) - Date.parse(cardB.due);
  });

  const random = createRandom(seed);
  const takenDue = sortedDue.slice(0, limit);
  const remaining = Math.max(0, limit - takenDue.length);
  const takenNew = shuffle(fresh, random).slice(0, remaining);

  return {
    items: [...takenDue, ...takenNew],
    dueCount: takenDue.length,
    newCount: takenNew.length,
  };
}

/** レベル内の学習状況。ダッシュボードとレベル選択画面で使う。 */
export interface LevelStats {
  readonly total: number;
  readonly seen: number;
  readonly due: number;
  /** 定着済み（間隔 21 日以上）の語数。 */
  readonly settled: number;
  /** 0..1 の到達度。定着済み語数 ÷ 収録語数。 */
  readonly completion: number;
}

const SETTLED_INTERVAL_DAYS = 21;

export function levelStats(progress: Progress, level: LevelId, now: Date): LevelStats {
  const pool = wordsForLevel(level);
  let seen = 0;
  let due = 0;
  let settled = 0;
  for (const word of pool) {
    const card = progress.cards[word.id];
    if (!card) {
      continue;
    }
    seen += 1;
    if (isDue(card, now)) {
      due += 1;
    }
    if (card.interval >= SETTLED_INTERVAL_DAYS) {
      settled += 1;
    }
  }
  return {
    total: pool.length,
    seen,
    due,
    settled,
    completion: pool.length === 0 ? 0 : settled / pool.length,
  };
}

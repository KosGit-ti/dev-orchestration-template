/**
 * 間隔反復のスケジューラ（SM-2 派生）。
 *
 * オリジナルの SM-2 を次の 2 点で変えている。
 *   1. 評価は 4 段階（again / hard / good / easy）に絞る。6 段階は自己申告の
 *      ぶれが大きく、個人利用では区別が機能しないため。
 *   2. 初回と 2 回目の間隔を固定値で持つ。学習開始直後に間隔が伸びすぎて
 *      定着前に忘れる挙動を避けるため。
 *
 * 時刻は呼び出し側から渡す。テストを決定的にするため、この層では now() を読まない。
 */

export type Grade = "again" | "hard" | "good" | "easy";

export interface SrsCard {
  /** 復習間隔（日）。0 は未学習または当日再出題。 */
  readonly interval: number;
  /** 易しさ係数。SM-2 の EF に相当し、1.3 を下限とする。 */
  readonly ease: number;
  /** 連続正答回数。again で 0 に戻る。 */
  readonly repetitions: number;
  /** 次回出題日時（ISO 8601）。 */
  readonly due: string;
  /** 直近の評価。UI で「苦手」を示すために持つ。 */
  readonly lastGrade: Grade | null;
  /** 累積の解答回数と誤答回数。習熟度の表示に使う。 */
  readonly reviews: number;
  readonly lapses: number;
}

export const MIN_EASE = 1.3;
const DAY_MS = 24 * 60 * 60 * 1000;

/** 未学習カードの初期状態。 */
export function newCard(now: Date): SrsCard {
  return {
    interval: 0,
    ease: 2.5,
    repetitions: 0,
    due: now.toISOString(),
    lastGrade: null,
    reviews: 0,
    lapses: 0,
  };
}

/** 評価から易しさ係数の増減を決める。 */
function easeDelta(grade: Grade): number {
  switch (grade) {
    case "again":
      return -0.2;
    case "hard":
      return -0.15;
    case "good":
      return 0;
    case "easy":
      return 0.15;
  }
}

/** 次の間隔（日）を決める。 */
function nextInterval(card: SrsCard, grade: Grade, ease: number): number {
  if (grade === "again") {
    // 当日中に再出題する。0 日は「同一セッション内で再び出す」を意味する。
    return 0;
  }
  if (card.repetitions === 0) {
    return grade === "easy" ? 3 : 1;
  }
  if (card.repetitions === 1) {
    return grade === "hard" ? 3 : grade === "easy" ? 8 : 6;
  }
  const factor = grade === "hard" ? 1.2 : ease;
  return Math.max(1, Math.round(card.interval * factor));
}

/** 1 回の解答をカードへ適用する。副作用を持たず、新しいカードを返す。 */
export function review(card: SrsCard, grade: Grade, now: Date): SrsCard {
  const ease = Math.max(MIN_EASE, Number((card.ease + easeDelta(grade)).toFixed(2)));
  const repetitions = grade === "again" ? 0 : card.repetitions + 1;
  const interval = nextInterval(card, grade, ease);
  return {
    interval,
    ease,
    repetitions,
    due: new Date(now.getTime() + interval * DAY_MS).toISOString(),
    lastGrade: grade,
    reviews: card.reviews + 1,
    lapses: card.lapses + (grade === "again" ? 1 : 0),
  };
}

/** 出題対象かどうか。 */
export function isDue(card: SrsCard, now: Date): boolean {
  return Date.parse(card.due) <= now.getTime();
}

/**
 * 習熟度（0..1）。
 * 間隔 21 日を「定着した」と見なす閾値に置き、そこまでを線形に伸ばす。
 */
export function mastery(card: SrsCard): number {
  const settled = 21;
  return Math.min(1, card.interval / settled);
}

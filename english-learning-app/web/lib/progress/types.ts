import type { SrsCard } from "../srs/sm2";
import type { LevelId } from "../content/types";

/** 1 日の学習記録。連続学習日数とグラフに使う。 */
export interface DailyRecord {
  /** `YYYY-MM-DD`（端末のローカル日付）。 */
  readonly date: string;
  readonly wordsReviewed: number;
  readonly wordsCorrect: number;
  readonly passagesCompleted: number;
  /** 学習時間の概算（秒）。 */
  readonly seconds: number;
}

/** パッセージ 1 本の到達状況。 */
export interface PassageProgress {
  readonly passageId: string;
  readonly completedAt: string;
  /** 穴埋め・読解を合わせた正答率（0..1）。 */
  readonly accuracy: number;
  readonly attempts: number;
}

/** プレースメント（レベル判定）の結果。 */
export interface PlacementResult {
  readonly completedAt: string;
  readonly level: LevelId;
  /** 正答数と出題数。判定の根拠を残す。 */
  readonly correct: number;
  readonly total: number;
}

/**
 * 利用者ごとの学習進捗。
 *
 * `version` はスキーマ移行のための必須フィールドである。増やすときは
 * 必ず migrate() へ変換を足し、テストを同一変更に含める（P-104）。
 */
export interface Progress {
  readonly version: 2;
  readonly userId: string;
  /** 単語 ID → SRS カード。 */
  readonly cards: Readonly<Record<string, SrsCard>>;
  /** パッセージ ID → 到達状況。 */
  readonly passages: Readonly<Record<string, PassageProgress>>;
  readonly placement: PlacementResult | null;
  /** 1 日に解く単語数の目標。 */
  readonly dailyGoal: number;
  /** 直近の日次記録（新しい順、最大 180 件）。 */
  readonly daily: readonly DailyRecord[];
  readonly updatedAt: string;
}

export const DEFAULT_DAILY_GOAL = 20;
export const MAX_DAILY_RECORDS = 180;

import type { LevelDefinition, LevelId } from "./types";
import { LEVEL_IDS } from "./types";

/**
 * レベル体系の正本。
 *
 * CEFR を軸に置き、TOEIC L&R / TOEFL iBT のスコア帯を対応付ける。スコア帯は
 * 試験実施団体が公表する CEFR 対応表を踏まえた目安であり、合否保証ではない。
 * 最上位 L6 は TOEIC 990 / TOEFL 120（いずれも満点）を上端に置く。
 *
 * 詳細な根拠と改訂履歴は docs/level-framework.md を正本とする。
 */
export const LEVELS: readonly LevelDefinition[] = [
  {
    id: 1,
    name: "Starter",
    cefr: "A1",
    toeic: [120, 400],
    toefl: null,
    targetVocabulary: 1000,
    canDo: "Understand everyday words and very short sentences about familiar things.",
    accent: "56 189 248",
  },
  {
    id: 2,
    name: "Explorer",
    cefr: "A2",
    toeic: [400, 550],
    toefl: null,
    targetVocabulary: 2000,
    canDo: "Follow short, plain descriptions of routine matters without translating them.",
    accent: "45 212 191",
  },
  {
    id: 3,
    name: "Bridge",
    cefr: "B1",
    toeic: [550, 700],
    toefl: [42, 71],
    targetVocabulary: 3500,
    canDo: "Read straightforward factual texts and hold the meaning in English.",
    accent: "132 204 22",
  },
  {
    id: 4,
    name: "Fluent",
    cefr: "B2",
    toeic: [700, 850],
    toefl: [72, 94],
    targetVocabulary: 6000,
    canDo: "Follow argument and detail in articles written for a general readership.",
    accent: "250 204 21",
  },
  {
    id: 5,
    name: "Advanced",
    cefr: "C1",
    toeic: [850, 950],
    toefl: [95, 113],
    targetVocabulary: 10000,
    canDo: "Read long, demanding texts and catch implicit meaning and tone.",
    accent: "251 146 60",
  },
  {
    id: 6,
    name: "Mastery",
    cefr: "C2",
    toeic: [950, 990],
    toefl: [114, 120],
    targetVocabulary: 15000,
    canDo: "Read anything written in English, including abstract and idiomatic prose.",
    accent: "244 114 182",
  },
] as const;

/** レベル ID から定義を引く。未知の ID は型で弾くため実行時 fallback を持たない。 */
export function getLevel(id: LevelId): LevelDefinition {
  const found = LEVELS.find((level) => level.id === id);
  if (!found) {
    // LevelId は 1..6 のリテラル union なのでここへは到達しない。
    // データ側の破損を早期に落とすため、黙って既定値へ倒さない（P-010）。
    throw new Error(`未定義のレベル ID です: ${String(id)}`);
  }
  return found;
}

/** 最上位レベル（TOEIC 990 / TOEFL 120 相当）。 */
export const MAX_LEVEL: LevelId = 6;

/** スコア帯を UI 表示用の文字列にする。TOEFL 範囲外のレベルは省略する。 */
export function formatScoreBand(level: LevelDefinition): string {
  const toeic = `TOEIC ${level.toeic[0]}–${level.toeic[1]}`;
  if (!level.toefl) {
    return toeic;
  }
  return `${toeic} / TOEFL ${level.toefl[0]}–${level.toefl[1]}`;
}

/** 最上位レベルかどうか。UI で「満点レンジ」を示すために使う。 */
export function isMaxLevel(id: LevelId): boolean {
  return id === MAX_LEVEL;
}

/** 次のレベル。最上位なら null。 */
export function nextLevel(id: LevelId): LevelId | null {
  const index = LEVEL_IDS.indexOf(id);
  const next = LEVEL_IDS[index + 1];
  return next ?? null;
}

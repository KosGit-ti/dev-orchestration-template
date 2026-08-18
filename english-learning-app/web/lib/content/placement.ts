import type { LevelId, WordEntry } from "./types";
import { LEVEL_IDS } from "./types";
import { wordsForLevel } from "./index";
import { buildWordQuestion, type WordQuestion } from "./quiz";
import { createRandom, shuffle } from "../util/shuffle";

/** 各レベルから何問出すか。全レベルを等しく踏むことで上限も下限も測れる。 */
export const QUESTIONS_PER_LEVEL = 4;

export interface PlacementQuestion extends WordQuestion {
  readonly level: LevelId;
}

/**
 * プレースメント問題を作る。
 *
 * 全 6 レベルから同数ずつ出し、易しい順に並べる。難しい順に並べると
 * 序盤の連続不正解で離脱しやすいため、易→難の順を採る。
 */
export function buildPlacementTest(seed: number): readonly PlacementQuestion[] {
  const random = createRandom(seed);
  return LEVEL_IDS.flatMap((level) => {
    const pool = shuffle(wordsForLevel(level), random).slice(0, QUESTIONS_PER_LEVEL);
    return pool.map((word: WordEntry, index) => ({
      ...buildWordQuestion(word, index % 2 === 0 ? "definition-to-word" : "word-to-definition"),
      level,
    }));
  });
}

/**
 * 解答結果から開始レベルを決める。
 *
 * 判定規則: 正答率が 75% 以上の最も高いレベルを到達レベルとし、その次を
 * 開始レベルにする。どのレベルも 75% に届かない場合は L1 から始める。
 * 「解ける最高レベル」ではなく「その一つ上」を返すのは、既に解ける範囲を
 * 反復させても学習にならないためである。
 */
export function resolvePlacementLevel(
  questions: readonly PlacementQuestion[],
  correctByIndex: readonly boolean[],
): LevelId {
  const tally = new Map<LevelId, { correct: number; total: number }>();
  questions.forEach((question, index) => {
    const bucket = tally.get(question.level) ?? { correct: 0, total: 0 };
    bucket.total += 1;
    if (correctByIndex[index]) {
      bucket.correct += 1;
    }
    tally.set(question.level, bucket);
  });

  let achieved: LevelId | null = null;
  for (const level of LEVEL_IDS) {
    const bucket = tally.get(level);
    if (bucket && bucket.total > 0 && bucket.correct / bucket.total >= 0.75) {
      achieved = level;
    }
  }
  if (achieved === null) {
    return 1;
  }
  const index = LEVEL_IDS.indexOf(achieved);
  return LEVEL_IDS[index + 1] ?? achieved;
}

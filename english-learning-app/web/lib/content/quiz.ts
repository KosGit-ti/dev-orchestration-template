import type { WordEntry } from "./types";
import { ALL_WORDS, wordsForLevel } from "./index";
import { createRandom, seedFrom, shuffle } from "../util/shuffle";

/** 4 択問題。定義から見出し語を選ぶ形式と、見出し語から定義を選ぶ形式を持つ。 */
export interface WordQuestion {
  readonly word: WordEntry;
  /** 出題の向き。両方向を混ぜないと「見た目で覚える」抜け道が残る。 */
  readonly direction: "definition-to-word" | "word-to-definition";
  readonly prompt: string;
  readonly options: readonly string[];
  readonly answerIndex: number;
}

/**
 * 誤答選択肢を同レベルの語から作る。
 *
 * レベルをまたいで選ぶと難度が下がりすぎる（易しい語が混ざると消去法で解ける）。
 * 同レベルに足りない場合だけ隣接レベルから補う。
 */
function distractorPool(word: WordEntry): readonly WordEntry[] {
  const sameLevel = wordsForLevel(word.level).filter((candidate) => candidate.id !== word.id);
  if (sameLevel.length >= 3) {
    return sameLevel;
  }
  return ALL_WORDS.filter((candidate) => candidate.id !== word.id);
}

/**
 * 1 語から 4 択問題を作る。
 * seed を与えると出題順と選択肢配置が決定的になるため、テストで固定できる。
 */
export function buildWordQuestion(
  word: WordEntry,
  direction: WordQuestion["direction"],
  seed = seedFrom(word.id + direction),
): WordQuestion {
  const random = createRandom(seed);
  const distractors = shuffle(distractorPool(word), random).slice(0, 3);
  const toText = (entry: WordEntry) =>
    direction === "definition-to-word" ? entry.headword : entry.definition;
  const options = shuffle([word, ...distractors], random).map(toText);
  return {
    word,
    direction,
    prompt: direction === "definition-to-word" ? word.definition : word.headword,
    options,
    answerIndex: options.indexOf(toText(word)),
  };
}

/** 出題の向きを語 ID から決める。同じ語は常に同じ向きから始まる。 */
export function directionFor(wordId: string, round: number): WordQuestion["direction"] {
  return (seedFrom(wordId) + round) % 2 === 0 ? "definition-to-word" : "word-to-definition";
}

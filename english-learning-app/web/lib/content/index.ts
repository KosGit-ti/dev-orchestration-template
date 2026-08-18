import words1 from "../../content/words/level-1.json";
import words2 from "../../content/words/level-2.json";
import words3 from "../../content/words/level-3.json";
import words4 from "../../content/words/level-4.json";
import words5 from "../../content/words/level-5.json";
import words6 from "../../content/words/level-6.json";
import passages1 from "../../content/passages/level-1.json";
import passages2 from "../../content/passages/level-2.json";
import passages3 from "../../content/passages/level-3.json";
import passages4 from "../../content/passages/level-4.json";
import passages5 from "../../content/passages/level-5.json";
import passages6 from "../../content/passages/level-6.json";
import type { LevelId, PassageEntry, WordEntry } from "./types";

/**
 * コンテンツの読み込み。
 *
 * 静的エクスポートのためデータはビルド時に埋め込む。JSON をそのまま import し、
 * 型は境界で一度だけ与える。実行時の形状検査はビルド前の
 * `npm run validate:content` が担うので、ここでは再検査しない。
 */
const WORDS_BY_LEVEL: Readonly<Record<LevelId, readonly WordEntry[]>> = {
  1: words1 as readonly WordEntry[],
  2: words2 as readonly WordEntry[],
  3: words3 as readonly WordEntry[],
  4: words4 as readonly WordEntry[],
  5: words5 as readonly WordEntry[],
  6: words6 as readonly WordEntry[],
};

const PASSAGES_BY_LEVEL: Readonly<Record<LevelId, readonly PassageEntry[]>> = {
  1: passages1 as readonly PassageEntry[],
  2: passages2 as readonly PassageEntry[],
  3: passages3 as readonly PassageEntry[],
  4: passages4 as readonly PassageEntry[],
  5: passages5 as readonly PassageEntry[],
  6: passages6 as readonly PassageEntry[],
};

export const ALL_WORDS: readonly WordEntry[] = Object.values(WORDS_BY_LEVEL).flat();
export const ALL_PASSAGES: readonly PassageEntry[] = Object.values(PASSAGES_BY_LEVEL).flat();

export function wordsForLevel(level: LevelId): readonly WordEntry[] {
  return WORDS_BY_LEVEL[level];
}

export function passagesForLevel(level: LevelId): readonly PassageEntry[] {
  return PASSAGES_BY_LEVEL[level];
}

export function findWord(id: string): WordEntry | null {
  return ALL_WORDS.find((word) => word.id === id) ?? null;
}

export function findPassage(id: string): PassageEntry | null {
  return ALL_PASSAGES.find((passage) => passage.id === id) ?? null;
}

/** レベルごとの収録数。学習画面と管理画面で同じ数字を出すために公開する。 */
export function contentCounts(level: LevelId): { words: number; passages: number } {
  return { words: wordsForLevel(level).length, passages: passagesForLevel(level).length };
}

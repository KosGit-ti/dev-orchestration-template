import {
  ALL_PASSAGES,
  ALL_WORDS,
  contentCounts,
  findWord,
  passagesForLevel,
  wordsForLevel,
} from "@/lib/content/index";
import {
  LEVELS,
  MAX_LEVEL,
  formatScoreBand,
  getLevel,
  isMaxLevel,
  nextLevel,
} from "@/lib/content/levels";
import { LEVEL_IDS } from "@/lib/content/types";

/** ひらがな・カタカナ・漢字。学習コンテンツへの混入は P-101 違反。 */
const JAPANESE = /[぀-ゟ゠-ヿ一-鿿]/;

describe("レベル体系", () => {
  it("6 レベルすべてが定義されている", () => {
    expect(LEVELS).toHaveLength(6);
    expect(LEVELS.map((level) => level.id)).toEqual([...LEVEL_IDS]);
  });

  it("最上位は TOEIC 990 / TOEFL 120 を上端に持つ", () => {
    const top = getLevel(MAX_LEVEL);
    expect(top.toeic[1]).toBe(990);
    expect(top.toefl?.[1]).toBe(120);
    expect(isMaxLevel(top.id)).toBe(true);
  });

  it("TOEIC のスコア帯はレベル順に上がり、隣と重ならない", () => {
    for (let i = 1; i < LEVELS.length; i += 1) {
      const previous = LEVELS[i - 1];
      const current = LEVELS[i];
      expect(current?.toeic[0]).toBeGreaterThanOrEqual(previous?.toeic[1] ?? 0);
    }
  });

  it("目安語彙はレベルとともに単調増加する", () => {
    const sizes = LEVELS.map((level) => level.targetVocabulary);
    expect([...sizes].sort((a, b) => a - b)).toEqual(sizes);
  });

  it("A1 と A2 は TOEFL の測定範囲外として null を持つ", () => {
    expect(getLevel(1).toefl).toBeNull();
    expect(getLevel(2).toefl).toBeNull();
    expect(getLevel(3).toefl).not.toBeNull();
  });

  it("スコア帯の表示は TOEFL の有無で切り替わる", () => {
    expect(formatScoreBand(getLevel(1))).not.toContain("TOEFL");
    expect(formatScoreBand(getLevel(6))).toContain("TOEFL 114–120");
  });

  it("最上位の次のレベルは存在しない", () => {
    expect(nextLevel(5)).toBe(6);
    expect(nextLevel(MAX_LEVEL)).toBeNull();
  });
});

describe("学習コンテンツ", () => {
  it("すべてのレベルに語とパッセージが入っている", () => {
    for (const level of LEVEL_IDS) {
      const counts = contentCounts(level);
      expect(counts.words).toBeGreaterThan(0);
      expect(counts.passages).toBeGreaterThan(0);
    }
  });

  it("語の ID は全レベルを通じて一意である", () => {
    const ids = ALL_WORDS.map((word) => word.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("見出し語はレベルをまたいでも重複しない", () => {
    const headwords = ALL_WORDS.map((word) => word.headword.toLowerCase());
    expect(new Set(headwords).size).toBe(headwords.length);
  });

  it("語の level フィールドは所属レベルと一致する", () => {
    for (const level of LEVEL_IDS) {
      for (const word of wordsForLevel(level)) {
        expect(word.level).toBe(level);
      }
    }
  });

  it("すべての語が英英定義と用例を持つ", () => {
    for (const word of ALL_WORDS) {
      expect(word.definition.trim().length).toBeGreaterThan(0);
      expect(word.examples.length).toBeGreaterThan(0);
    }
  });

  it("学習コンテンツに日本語が混入していない（P-101）", () => {
    for (const word of ALL_WORDS) {
      expect(JSON.stringify(word)).not.toMatch(JAPANESE);
    }
    for (const passage of ALL_PASSAGES) {
      expect(JSON.stringify(passage)).not.toMatch(JAPANESE);
    }
  });

  it("パッセージの空所と本文のプレースホルダが対応している", () => {
    for (const passage of ALL_PASSAGES) {
      const placeholders = [...passage.text.matchAll(/\{\{(\d+)\}\}/g)].map((m) => Number(m[1]));
      expect(placeholders).toEqual(passage.clozes.map((_, index) => index + 1));
    }
  });

  it("穴埋めの選択肢に正解が含まれている", () => {
    for (const passage of ALL_PASSAGES) {
      for (const cloze of passage.clozes) {
        expect(cloze.options).toContain(cloze.answer);
        expect(new Set(cloze.options).size).toBe(cloze.options.length);
      }
    }
  });

  it("読解設問の answerIndex が選択肢の範囲に収まっている", () => {
    for (const passage of ALL_PASSAGES) {
      expect(passage.questions.length).toBeGreaterThan(0);
      for (const question of passage.questions) {
        expect(question.answerIndex).toBeGreaterThanOrEqual(0);
        expect(question.answerIndex).toBeLessThan(question.options.length);
        expect(question.rationale.trim().length).toBeGreaterThan(0);
      }
    }
  });

  it("パッセージの level フィールドは所属レベルと一致する", () => {
    for (const level of LEVEL_IDS) {
      for (const passage of passagesForLevel(level)) {
        expect(passage.level).toBe(level);
      }
    }
  });

  it("存在しない ID を引くと null を返す", () => {
    expect(findWord("w-does-not-exist")).toBeNull();
    expect(findWord(ALL_WORDS[0]?.id ?? "")).not.toBeNull();
  });
});

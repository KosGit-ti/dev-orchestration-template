import { buildWordQuestion, directionFor } from "@/lib/content/quiz";
import { wordsForLevel } from "@/lib/content/index";
import {
  buildPlacementTest,
  resolvePlacementLevel,
  QUESTIONS_PER_LEVEL,
} from "@/lib/content/placement";
import { LEVEL_IDS, type LevelId } from "@/lib/content/types";

describe("単語問題の生成", () => {
  const word = wordsForLevel(3)[0]!;

  it("選択肢は 4 件で、正解を 1 つだけ含む", () => {
    const question = buildWordQuestion(word, "definition-to-word");
    expect(question.options).toHaveLength(4);
    expect(question.options[question.answerIndex]).toBe(word.headword);
    expect(new Set(question.options).size).toBe(4);
  });

  it("向きを変えると出題文と選択肢が入れ替わる", () => {
    const forward = buildWordQuestion(word, "definition-to-word");
    const backward = buildWordQuestion(word, "word-to-definition");
    expect(forward.prompt).toBe(word.definition);
    expect(backward.prompt).toBe(word.headword);
    expect(backward.options[backward.answerIndex]).toBe(word.definition);
  });

  it("誤答は同じレベルの語から選ぶ", () => {
    const question = buildWordQuestion(word, "definition-to-word");
    const sameLevel = new Set(wordsForLevel(3).map((entry) => entry.headword));
    for (const option of question.options) {
      expect(sameLevel.has(option)).toBe(true);
    }
  });

  it("同じ seed なら同じ並びを返す（テストを決定的にできる）", () => {
    const a = buildWordQuestion(word, "definition-to-word", 42);
    const b = buildWordQuestion(word, "definition-to-word", 42);
    expect(a.options).toEqual(b.options);
    expect(a.answerIndex).toBe(b.answerIndex);
  });

  it("出題の向きはラウンドごとに入れ替わる", () => {
    expect(directionFor("w-l3-001", 0)).not.toBe(directionFor("w-l3-001", 1));
  });
});

describe("プレースメント", () => {
  const questions = buildPlacementTest(7);

  it("全レベルから同数ずつ出題する", () => {
    expect(questions).toHaveLength(LEVEL_IDS.length * QUESTIONS_PER_LEVEL);
    for (const level of LEVEL_IDS) {
      expect(questions.filter((question) => question.level === level)).toHaveLength(
        QUESTIONS_PER_LEVEL,
      );
    }
  });

  it("易しい順に並べる", () => {
    const levels = questions.map((question) => question.level);
    expect([...levels].sort((a, b) => a - b)).toEqual(levels);
  });

  function answersFor(threshold: LevelId | 0): boolean[] {
    // threshold 以下のレベルを全問正解、それより上を全問不正解とする。
    return questions.map((question) => question.level <= threshold);
  }

  it("全問不正解なら L1 から始める", () => {
    expect(resolvePlacementLevel(questions, answersFor(0))).toBe(1);
  });

  it("解けた最高レベルの一つ上を開始点にする", () => {
    expect(resolvePlacementLevel(questions, answersFor(1))).toBe(2);
    expect(resolvePlacementLevel(questions, answersFor(3))).toBe(4);
    expect(resolvePlacementLevel(questions, answersFor(5))).toBe(6);
  });

  it("最上位まで解けた場合は最上位に留める", () => {
    expect(resolvePlacementLevel(questions, answersFor(6))).toBe(6);
  });

  it("正答率が 75% に届かないレベルは到達扱いにしない", () => {
    // L1 を 4 問中 2 問（50%）だけ正解させる。
    let seen = 0;
    const answers = questions.map((question) => {
      if (question.level !== 1) {
        return false;
      }
      seen += 1;
      return seen <= 2;
    });
    expect(resolvePlacementLevel(questions, answers)).toBe(1);
  });
});

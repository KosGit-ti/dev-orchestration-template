import { levelStats, planSession } from "@/lib/progress/session";
import { emptyProgress, recordWordReview } from "@/lib/progress/store";
import { wordsForLevel } from "@/lib/content/index";
import type { Progress } from "@/lib/progress/types";

const NOW = new Date("2026-08-18T09:00:00.000Z");
const DAY = 24 * 60 * 60 * 1000;

function withCard(
  progress: Progress,
  wordId: string,
  interval: number,
  dueOffsetMs: number,
): Progress {
  return {
    ...progress,
    cards: {
      ...progress.cards,
      [wordId]: {
        interval,
        ease: 2.5,
        repetitions: 2,
        due: new Date(NOW.getTime() + dueOffsetMs).toISOString(),
        lastGrade: "good",
        reviews: 2,
        lapses: 0,
      },
    },
  };
}

describe("セッションの組み立て", () => {
  const words = wordsForLevel(2);

  it("未学習の語だけの状態では新規のみを出す", () => {
    const plan = planSession(emptyProgress("u", NOW), 2, 5, NOW, 1);
    expect(plan.items).toHaveLength(5);
    expect(plan.dueCount).toBe(0);
    expect(plan.newCount).toBe(5);
  });

  it("期限が来た復習を新規より先に出す", () => {
    let progress = emptyProgress("u", NOW);
    progress = withCard(progress, words[0]!.id, 3, -2 * DAY);
    progress = withCard(progress, words[1]!.id, 3, -1 * DAY);
    const plan = planSession(progress, 2, 5, NOW, 1);
    expect(plan.dueCount).toBe(2);
    expect(plan.items[0]?.id).toBe(words[0]!.id);
    expect(plan.items[1]?.id).toBe(words[1]!.id);
  });

  it("復習だけで枠が埋まる日は新規を足さない", () => {
    let progress = emptyProgress("u", NOW);
    for (const word of words.slice(0, 6)) {
      progress = withCard(progress, word.id, 3, -DAY);
    }
    const plan = planSession(progress, 2, 4, NOW, 1);
    expect(plan.items).toHaveLength(4);
    expect(plan.newCount).toBe(0);
  });

  it("期限前のカードは出題しない", () => {
    let progress = emptyProgress("u", NOW);
    for (const word of words) {
      progress = withCard(progress, word.id, 10, +5 * DAY);
    }
    const plan = planSession(progress, 2, 10, NOW, 1);
    expect(plan.items).toHaveLength(0);
  });

  it("同じ seed なら同じ新規語を選ぶ", () => {
    const a = planSession(emptyProgress("u", NOW), 2, 5, NOW, 99);
    const b = planSession(emptyProgress("u", NOW), 2, 5, NOW, 99);
    expect(a.items.map((item) => item.id)).toEqual(b.items.map((item) => item.id));
  });
});

describe("レベル別の到達度", () => {
  const words = wordsForLevel(1);

  it("未学習なら到達度 0", () => {
    const stats = levelStats(emptyProgress("u", NOW), 1, NOW);
    expect(stats.seen).toBe(0);
    expect(stats.settled).toBe(0);
    expect(stats.completion).toBe(0);
    expect(stats.total).toBe(words.length);
  });

  it("間隔 21 日以上の語だけを定着として数える", () => {
    let progress = emptyProgress("u", NOW);
    progress = withCard(progress, words[0]!.id, 21, +DAY);
    progress = withCard(progress, words[1]!.id, 20, +DAY);
    const stats = levelStats(progress, 1, NOW);
    expect(stats.seen).toBe(2);
    expect(stats.settled).toBe(1);
    expect(stats.completion).toBeCloseTo(1 / words.length);
  });

  it("期限が来た語を復習待ちとして数える", () => {
    let progress = emptyProgress("u", NOW);
    progress = recordWordReview(progress, words[0]!.id, "again", NOW);
    expect(levelStats(progress, 1, NOW).due).toBe(1);
  });
});

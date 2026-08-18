import {
  currentStreak,
  emptyProgress,
  localDateKey,
  migrate,
  recordPassage,
  recordWordReview,
  setDailyGoal,
  todayRecord,
} from "@/lib/progress/store";
import { DEFAULT_DAILY_GOAL, type DailyRecord } from "@/lib/progress/types";

const NOW = new Date("2026-08-18T09:00:00.000Z");
const DAY = 24 * 60 * 60 * 1000;

describe("進捗ストア", () => {
  it("単語の解答が日次記録とカードへ同時に反映される", () => {
    const base = emptyProgress("u-test", NOW);
    const after = recordWordReview(base, "w-l1-001", "good", NOW, 12);
    expect(after.cards["w-l1-001"]?.reviews).toBe(1);
    expect(after.daily[0]?.wordsReviewed).toBe(1);
    expect(after.daily[0]?.wordsCorrect).toBe(1);
    expect(after.daily[0]?.seconds).toBe(12);
  });

  it("again は正答数へ数えない", () => {
    const after = recordWordReview(emptyProgress("u", NOW), "w-l1-001", "again", NOW);
    expect(after.daily[0]?.wordsReviewed).toBe(1);
    expect(after.daily[0]?.wordsCorrect).toBe(0);
  });

  it("同じ日の解答は 1 件の日次記録へまとめる", () => {
    let progress = emptyProgress("u", NOW);
    progress = recordWordReview(progress, "a", "good", NOW);
    progress = recordWordReview(progress, "b", "good", NOW);
    expect(progress.daily).toHaveLength(1);
    expect(progress.daily[0]?.wordsReviewed).toBe(2);
  });

  it("日をまたぐと新しい日次記録を先頭へ積む", () => {
    let progress = recordWordReview(emptyProgress("u", NOW), "a", "good", NOW);
    const tomorrow = new Date(NOW.getTime() + DAY);
    progress = recordWordReview(progress, "b", "good", tomorrow);
    expect(progress.daily).toHaveLength(2);
    expect(progress.daily[0]?.date).toBe(localDateKey(tomorrow));
  });

  it("パッセージの再挑戦では良いほうの正答率を残す", () => {
    let progress = recordPassage(emptyProgress("u", NOW), "p-l1-001", 0.9, NOW);
    progress = recordPassage(progress, "p-l1-001", 0.4, NOW);
    expect(progress.passages["p-l1-001"]?.accuracy).toBe(0.9);
    expect(progress.passages["p-l1-001"]?.attempts).toBe(2);
  });

  it("目標語数は 5〜200 の範囲へ丸める", () => {
    const base = emptyProgress("u", NOW);
    expect(setDailyGoal(base, 1, NOW).dailyGoal).toBe(5);
    expect(setDailyGoal(base, 999, NOW).dailyGoal).toBe(200);
    expect(setDailyGoal(base, 33.4, NOW).dailyGoal).toBe(33);
  });

  it("今日の記録が無ければ 0 の記録を返す", () => {
    expect(todayRecord([], NOW).wordsReviewed).toBe(0);
  });
});

describe("連続学習日数", () => {
  function record(date: string, words = 1): DailyRecord {
    return { date, wordsReviewed: words, wordsCorrect: words, passagesCompleted: 0, seconds: 0 };
  }

  it("記録が無ければ 0 になる", () => {
    expect(currentStreak([], NOW)).toBe(0);
  });

  it("今日から連続していれば日数を数える", () => {
    const daily = [record("2026-08-18"), record("2026-08-17"), record("2026-08-16")];
    expect(currentStreak(daily, new Date("2026-08-18T09:00:00"))).toBe(3);
  });

  it("今日が未学習でも昨日まで続いていれば継続扱いにする", () => {
    const daily = [record("2026-08-17"), record("2026-08-16")];
    expect(currentStreak(daily, new Date("2026-08-18T09:00:00"))).toBe(2);
  });

  it("2 日以上空くと途切れる", () => {
    const daily = [record("2026-08-15"), record("2026-08-14")];
    expect(currentStreak(daily, new Date("2026-08-18T09:00:00"))).toBe(0);
  });

  it("学習量 0 の日は連続に数えない", () => {
    const daily = [record("2026-08-18", 0), record("2026-08-17")];
    expect(currentStreak(daily, new Date("2026-08-18T09:00:00"))).toBe(1);
  });
});

describe("スキーマ移行", () => {
  it("v1 のカードと進捗を保持したまま v2 へ移す", () => {
    const v1 = {
      version: 1,
      userId: "u",
      cards: { a: { interval: 3 } },
      passages: {},
      placement: null,
      dailyGoal: 30,
    };
    const migrated = migrate(v1, "u", NOW);
    expect(migrated.version).toBe(2);
    expect(migrated.cards.a).toEqual({ interval: 3 });
    expect(migrated.dailyGoal).toBe(30);
    expect(migrated.daily).toEqual([]);
  });

  it("未知の版と壊れたデータは空の進捗へ倒す", () => {
    expect(migrate({ version: 99 }, "u", NOW).cards).toEqual({});
    expect(migrate(null, "u", NOW).dailyGoal).toBe(DEFAULT_DAILY_GOAL);
    expect(migrate("broken", "u", NOW).version).toBe(2);
  });

  it("v2 はそのまま通す", () => {
    const v2 = emptyProgress("u", NOW);
    expect(migrate(v2, "u", NOW)).toBe(v2);
  });
});

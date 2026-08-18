import { isDue, mastery, newCard, review, MIN_EASE, type SrsCard } from "@/lib/srs/sm2";

const NOW = new Date("2026-08-18T09:00:00.000Z");
const DAY = 24 * 60 * 60 * 1000;

describe("SRS スケジューラ", () => {
  it("未学習カードは即座に出題対象になる", () => {
    const card = newCard(NOW);
    expect(card.interval).toBe(0);
    expect(card.repetitions).toBe(0);
    expect(isDue(card, NOW)).toBe(true);
  });

  it("初回 good は 1 日後、初回 easy は 3 日後に出す", () => {
    const good = review(newCard(NOW), "good", NOW);
    expect(good.interval).toBe(1);
    expect(Date.parse(good.due)).toBe(NOW.getTime() + DAY);

    const easy = review(newCard(NOW), "easy", NOW);
    expect(easy.interval).toBe(3);
  });

  it("2 回目の good は 6 日後、hard は 3 日後に出す", () => {
    const first = review(newCard(NOW), "good", NOW);
    expect(review(first, "good", NOW).interval).toBe(6);
    expect(review(first, "hard", NOW).interval).toBe(3);
  });

  it("3 回目以降は ease 係数で間隔が伸びる", () => {
    let card = review(newCard(NOW), "good", NOW);
    card = review(card, "good", NOW);
    const third = review(card, "good", NOW);
    // interval 6 × ease 2.5 = 15
    expect(third.interval).toBe(15);
    expect(third.repetitions).toBe(3);
  });

  it("again は間隔を 0 に戻し、連続正答と ease を下げる", () => {
    let card = review(newCard(NOW), "good", NOW);
    card = review(card, "good", NOW);
    const lapsed = review(card, "again", NOW);
    expect(lapsed.interval).toBe(0);
    expect(lapsed.repetitions).toBe(0);
    expect(lapsed.lapses).toBe(1);
    expect(lapsed.ease).toBeLessThan(card.ease);
    expect(isDue(lapsed, NOW)).toBe(true);
  });

  it("ease は下限 1.3 を割らない", () => {
    let card = newCard(NOW);
    for (let i = 0; i < 40; i += 1) {
      card = review(card, "again", NOW);
    }
    expect(card.ease).toBe(MIN_EASE);
  });

  it("習熟度は間隔 21 日で 1 に達し、それ以上は 1 で頭打ちになる", () => {
    const base: SrsCard = { ...newCard(NOW), interval: 21 };
    expect(mastery(base)).toBe(1);
    expect(mastery({ ...base, interval: 60 })).toBe(1);
    expect(mastery({ ...base, interval: 0 })).toBe(0);
  });

  it("期限前のカードは出題対象にならない", () => {
    const card = review(newCard(NOW), "good", NOW);
    expect(isDue(card, NOW)).toBe(false);
    expect(isDue(card, new Date(NOW.getTime() + DAY))).toBe(true);
  });
});

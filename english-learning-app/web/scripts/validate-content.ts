/**
 * 学習コンテンツの検証。
 *
 * CI と完了ゲート G-5 から呼ばれる。落とすのは次の 4 種類である。
 *   1. 日本語（かな・漢字）の混入。第一目的に直結する制約（P-101）。
 *   2. ID の重複、レベル不一致、必須項目の欠落。
 *   3. 穴埋め設問と本文中プレースホルダの不一致。
 *   4. 選択肢に正解が含まれない、または重複している設問。
 *
 * 警告だけ出して通す項目は置かない。通るか落ちるかのどちらかにする。
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const CONTENT_DIR = join(import.meta.dirname ?? ".", "..", "content");

/** ひらがな・カタカナ・CJK 統合漢字。英数字と記号だけを許す。 */
const JAPANESE = /[぀-ゟ゠-ヿ一-鿿]/;

interface Problem {
  readonly file: string;
  readonly where: string;
  readonly message: string;
}

const problems: Problem[] = [];

function report(file: string, where: string, message: string): void {
  problems.push({ file, where, message });
}

function walkStrings(
  value: unknown,
  path: string,
  visit: (text: string, at: string) => void,
): void {
  if (typeof value === "string") {
    visit(value, path);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => walkStrings(item, `${path}[${index}]`, visit));
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      walkStrings(item, `${path}.${key}`, visit);
    }
  }
}

function loadJson(dir: string, file: string): unknown {
  return JSON.parse(readFileSync(join(CONTENT_DIR, dir, file), "utf-8"));
}

function levelFromFilename(file: string): number | null {
  const match = /^level-(\d)\.json$/.exec(file);
  return match?.[1] ? Number(match[1]) : null;
}

// ---- 単語 ----------------------------------------------------------------
const seenWordIds = new Set<string>();
const seenHeadwords = new Map<string, string>();

for (const file of readdirSync(join(CONTENT_DIR, "words")).sort()) {
  const level = levelFromFilename(file);
  if (level === null) {
    report(file, "-", "words/ には level-<1-6>.json だけを置く");
    continue;
  }
  const entries = loadJson("words", file) as Record<string, unknown>[];
  if (entries.length === 0) {
    report(file, "-", "語が 1 件も入っていない");
  }
  entries.forEach((entry, index) => {
    const at = `#${index} (${String(entry.headword ?? "?")})`;
    walkStrings(entry, "", (text, path) => {
      if (JAPANESE.test(text)) {
        report(file, `${at}${path}`, `日本語が混入している: ${text.slice(0, 40)}`);
      }
    });
    for (const field of ["id", "headword", "pos", "definition"]) {
      if (typeof entry[field] !== "string" || (entry[field] as string).trim() === "") {
        report(file, at, `必須項目 ${field} が無い`);
      }
    }
    if (!Array.isArray(entry.examples) || entry.examples.length === 0) {
      report(file, at, "examples が 1 件も無い");
    }
    if (entry.level !== level) {
      report(file, at, `level が ${String(entry.level)} だがファイルは L${level}`);
    }
    const id = String(entry.id);
    if (seenWordIds.has(id)) {
      report(file, at, `ID が重複している: ${id}`);
    }
    seenWordIds.add(id);
    const headword = String(entry.headword).toLowerCase();
    const previous = seenHeadwords.get(headword);
    if (previous) {
      report(file, at, `見出し語が ${previous} と重複している: ${headword}`);
    }
    seenHeadwords.set(headword, file);
  });
}

// ---- パッセージ ----------------------------------------------------------
const seenPassageIds = new Set<string>();

for (const file of readdirSync(join(CONTENT_DIR, "passages")).sort()) {
  const level = levelFromFilename(file);
  if (level === null) {
    report(file, "-", "passages/ には level-<1-6>.json だけを置く");
    continue;
  }
  const entries = loadJson("passages", file) as Record<string, unknown>[];
  entries.forEach((entry, index) => {
    const at = `#${index} (${String(entry.title ?? "?")})`;
    walkStrings(entry, "", (text, path) => {
      if (JAPANESE.test(text)) {
        report(file, `${at}${path}`, `日本語が混入している: ${text.slice(0, 40)}`);
      }
    });
    if (entry.level !== level) {
      report(file, at, `level が ${String(entry.level)} だがファイルは L${level}`);
    }
    const id = String(entry.id);
    if (seenPassageIds.has(id)) {
      report(file, at, `ID が重複している: ${id}`);
    }
    seenPassageIds.add(id);

    const text = String(entry.text ?? "");
    const clozes = (entry.clozes ?? []) as Record<string, unknown>[];
    const placeholders = [...text.matchAll(/\{\{(\d+)\}\}/g)].map((match) => Number(match[1]));
    const expected = clozes.map((_, i) => i + 1);
    if (placeholders.join(",") !== expected.join(",")) {
      report(
        file,
        at,
        `本文の空所 [${placeholders.join(",")}] と clozes 件数 ${clozes.length} が一致しない`,
      );
    }
    clozes.forEach((cloze, clozeIndex) => {
      const options = (cloze.options ?? []) as string[];
      if (!options.includes(String(cloze.answer))) {
        report(file, `${at} cloze#${clozeIndex}`, "options に answer が含まれていない");
      }
      if (new Set(options).size !== options.length) {
        report(file, `${at} cloze#${clozeIndex}`, "options に重複がある");
      }
      if (options.length < 3) {
        report(file, `${at} cloze#${clozeIndex}`, "options が 3 件未満で選択問題にならない");
      }
    });

    const questions = (entry.questions ?? []) as Record<string, unknown>[];
    if (questions.length === 0) {
      report(file, at, "読解設問が 1 件も無い");
    }
    questions.forEach((question, questionIndex) => {
      const options = (question.options ?? []) as string[];
      const answerIndex = question.answerIndex;
      if (typeof answerIndex !== "number" || answerIndex < 0 || answerIndex >= options.length) {
        report(file, `${at} q#${questionIndex}`, `answerIndex が範囲外: ${String(answerIndex)}`);
      }
      if (new Set(options).size !== options.length) {
        report(file, `${at} q#${questionIndex}`, "options に重複がある");
      }
      if (typeof question.rationale !== "string" || question.rationale.trim() === "") {
        report(file, `${at} q#${questionIndex}`, "rationale が空。正解の根拠を英語で書く");
      }
    });
  });
}

// ---- 結果 ----------------------------------------------------------------
if (problems.length > 0) {
  for (const problem of problems) {
    process.stderr.write(`FAIL ${problem.file} ${problem.where}: ${problem.message}\n`);
  }
  process.stderr.write(`\n検証に失敗しました（${problems.length} 件）。\n`);
  process.exit(1);
}

process.stdout.write(
  `コンテンツ検証 OK: 語 ${seenWordIds.size} 件 / パッセージ ${seenPassageIds.size} 件\n`,
);

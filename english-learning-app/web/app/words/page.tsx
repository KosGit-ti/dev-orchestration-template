"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Check, RotateCcw, Volume2, X } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Banner, Card, EmptyState, ProgressBar, SectionTitle } from "@/components/ui";
import { useAuth } from "@/lib/auth/context";
import { useProgress } from "@/lib/progress/use-progress";
import { recordWordReview } from "@/lib/progress/store";
import { planSession } from "@/lib/progress/session";
import { buildWordQuestion, directionFor, type WordQuestion } from "@/lib/content/quiz";
import { getLevel } from "@/lib/content/levels";
import { isSpeechSupported, speak } from "@/lib/tts/speak";
import { seedFrom } from "@/lib/util/shuffle";
import type { Grade } from "@/lib/srs/sm2";
import type { WordEntry } from "@/lib/content/types";
import { cn } from "@/lib/util/cn";

export default function WordsPage() {
  return (
    <AppShell>
      <WordTrainer />
    </AppShell>
  );
}

/** 評価ボタン。SM-2 の 4 段階を、学習者が自己申告しやすい語で示す。 */
const GRADES: readonly { grade: Grade; label: string; hint: string; tone: string }[] = [
  { grade: "again", label: "Again", hint: "分からなかった", tone: "text-danger" },
  { grade: "hard", label: "Hard", hint: "迷った", tone: "text-warning" },
  { grade: "good", label: "Good", hint: "思い出せた", tone: "text-fg" },
  { grade: "easy", label: "Easy", hint: "即答できた", tone: "text-success" },
];

function WordTrainer() {
  const { user } = useAuth();
  const { progress, update } = useProgress();
  /** セッション内で解き直す語を保持する。again はここへ戻す。 */
  const [queue, setQueue] = useState<readonly WordEntry[] | null>(null);
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [round, setRound] = useState(0);
  const [answered, setAnswered] = useState(0);
  const [correct, setCorrect] = useState(0);

  const level = user?.level ?? null;

  // セッションは一度だけ組み立てる。解答のたびに組み直すと出題が入れ替わってしまう。
  useEffect(() => {
    if (!progress || level === null || queue !== null) {
      return;
    }
    const plan = planSession(
      progress,
      level,
      progress.dailyGoal,
      new Date(),
      seedFrom(`${user?.id ?? ""}-${round}`),
    );
    setQueue(plan.items);
  }, [progress, level, queue, user?.id, round]);

  const current = queue?.[index] ?? null;

  const question: WordQuestion | null = useMemo(() => {
    if (!current) {
      return null;
    }
    return buildWordQuestion(
      current,
      directionFor(current.id, round),
      seedFrom(`${current.id}-${round}-${index}`),
    );
  }, [current, round, index]);

  const grade = useCallback(
    (value: Grade) => {
      if (!current || !queue) {
        return;
      }
      update((state, now) => recordWordReview(state, current.id, value, now));
      // again はセッション末尾へ戻し、同じ回のうちにもう一度出す。
      const rest = queue.slice(index + 1);
      const next = value === "again" ? [...rest, current] : rest;
      setQueue([...queue.slice(0, index + 1), ...next]);
      setIndex(index + 1);
      setSelected(null);
    },
    [current, queue, index, update],
  );

  const restart = useCallback(() => {
    setQueue(null);
    setIndex(0);
    setSelected(null);
    setAnswered(0);
    setCorrect(0);
    setRound((value) => value + 1);
  }, []);

  if (!user || !progress) {
    return <p className="text-sm text-muted">読み込み中…</p>;
  }

  if (level === null) {
    return (
      <EmptyState
        title="先にレベルを決めてください"
        description="レベル別モードのプレースメントを受けると、単語モードの出題範囲が決まります。"
      />
    );
  }

  const definition = getLevel(level);

  if (queue === null) {
    return <p className="text-sm text-muted">出題を準備しています…</p>;
  }

  if (!current || !question) {
    return (
      <div className="space-y-6">
        <SectionTitle title="単語モード" description={`L${level} ${definition.name}`} />
        <Card>
          <p className="font-medium">このセッションは終わりです。</p>
          <p className="mt-1.5 text-sm text-muted">
            {answered} 問中 {correct} 問正解（
            {answered === 0 ? 0 : Math.round((correct / answered) * 100)}%）。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={restart} className="btn-ghost">
              <RotateCcw className="h-4 w-4" aria-hidden />
              もう一度組み直す
            </button>
            <Link href="/sentences" className="btn-primary">
              文章モードへ
              <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          </div>
        </Card>
      </div>
    );
  }

  const revealed = selected !== null;
  const isCorrect = selected === question.answerIndex;

  function choose(optionIndex: number) {
    if (revealed || !question) {
      return;
    }
    setSelected(optionIndex);
    setAnswered((value) => value + 1);
    if (optionIndex === question.answerIndex) {
      setCorrect((value) => value + 1);
    }
  }

  const remaining = queue.length - index;

  return (
    <div className="space-y-6">
      <SectionTitle
        title="単語モード"
        description={`L${level} ${definition.name}・残り ${remaining} 語`}
      />
      <ProgressBar
        value={queue.length === 0 ? 0 : index / queue.length}
        accent={definition.accent}
        label="セッション進捗"
      />

      <Card className="animate-fade-in">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">
          {question.direction === "definition-to-word"
            ? "Which word matches this definition?"
            : "Which definition matches this word?"}
        </p>
        <p
          className={cn(
            "mt-3",
            question.direction === "definition-to-word"
              ? "reading text-lg"
              : "text-3xl font-semibold tracking-tight",
          )}
        >
          {question.prompt}
        </p>
        {question.direction === "word-to-definition" && current.ipa ? (
          <p className="mt-1.5 font-mono text-sm text-muted">{current.ipa}</p>
        ) : null}
        {question.direction === "word-to-definition" && isSpeechSupported() ? (
          <button
            type="button"
            onClick={() => speak(current.headword)}
            className="btn-ghost mt-3"
            aria-label={`${current.headword} を読み上げる`}
          >
            <Volume2 className="h-4 w-4" aria-hidden />
            Listen
          </button>
        ) : null}

        <div className="mt-5 space-y-2">
          {question.options.map((option, optionIndex) => {
            const isAnswer = optionIndex === question.answerIndex;
            const isPicked = optionIndex === selected;
            return (
              <button
                key={option}
                type="button"
                onClick={() => choose(optionIndex)}
                disabled={revealed}
                className={cn(
                  "flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                  !revealed && "border-border bg-surface hover:bg-surface-raised",
                  revealed && isAnswer && "border-success/50 bg-success/10",
                  revealed && isPicked && !isAnswer && "border-danger/50 bg-danger/10",
                  revealed && !isAnswer && !isPicked && "border-border bg-surface opacity-60",
                )}
              >
                {revealed && isAnswer ? (
                  <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
                ) : null}
                {revealed && isPicked && !isAnswer ? (
                  <X className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden />
                ) : null}
                <span className={question.direction === "definition-to-word" ? "font-medium" : ""}>
                  {option}
                </span>
              </button>
            );
          })}
        </div>
      </Card>

      {revealed ? (
        <>
          <WordDetail word={current} />
          <Card>
            <p className="text-sm text-muted">
              {isCorrect
                ? "どのくらい確実に思い出せましたか。"
                : "次にいつ出すかを決めます。分からなかった語はこの回のうちにもう一度出ます。"}
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {GRADES.map(({ grade: value, label, hint, tone }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => grade(value)}
                  className="btn-ghost flex-col items-start gap-0.5 py-3"
                >
                  <span className={cn("text-sm font-semibold", tone)}>{label}</span>
                  <span className="text-xs font-normal text-muted">{hint}</span>
                </button>
              ))}
            </div>
          </Card>
        </>
      ) : null}
    </div>
  );
}

/** 解答後に出す語の詳細。ここも英語だけで構成する（P-101）。 */
function WordDetail({ word }: { word: WordEntry }) {
  return (
    <Card className="animate-pop-in">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-2xl font-semibold tracking-tight">{word.headword}</h3>
        <span className="text-sm italic text-muted">{word.pos}</span>
        {word.ipa ? <span className="font-mono text-sm text-muted">{word.ipa}</span> : null}
        {word.register && word.register !== "neutral" ? (
          <span className="chip">{word.register}</span>
        ) : null}
        {isSpeechSupported() ? (
          <button
            type="button"
            onClick={() => speak(word.headword)}
            className="ml-auto text-muted transition-colors hover:text-fg"
            aria-label={`${word.headword} を読み上げる`}
          >
            <Volume2 className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
      </div>

      <p className="reading mt-3">{word.definition}</p>

      <dl className="mt-5 space-y-4 text-sm">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-muted">Examples</dt>
          <dd className="mt-1.5 space-y-1.5">
            {word.examples.map((example) => (
              <p key={example} className="reading text-[0.9375rem]">
                {example}
              </p>
            ))}
          </dd>
        </div>

        {word.collocations?.length ? (
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-muted">
              Common patterns
            </dt>
            <dd className="mt-1.5 flex flex-wrap gap-1.5">
              {word.collocations.map((item) => (
                <span key={item} className="chip">
                  {item}
                </span>
              ))}
            </dd>
          </div>
        ) : null}

        {word.synonyms?.length || word.antonyms?.length ? (
          <div className="flex flex-wrap gap-x-8 gap-y-3">
            {word.synonyms?.length ? (
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-muted">Near</dt>
                <dd className="mt-1 text-fg">{word.synonyms.join(", ")}</dd>
              </div>
            ) : null}
            {word.antonyms?.length ? (
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-muted">Opposite</dt>
                <dd className="mt-1 text-fg">{word.antonyms.join(", ")}</dd>
              </div>
            ) : null}
          </div>
        ) : null}
      </dl>

      <div className="mt-5">
        <Banner tone="info">
          <p className="text-xs">
            日本語訳は出しません。定義と用例だけで像が結べたかを、次の評価で自己申告してください。
          </p>
        </Banner>
      </div>
    </Card>
  );
}

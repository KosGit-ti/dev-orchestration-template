"use client";

import { useCallback, useMemo, useState } from "react";
import { Award, Check, Lock, Target } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Banner, Card, ProgressBar, SectionTitle } from "@/components/ui";
import { useAuth } from "@/lib/auth/context";
import { useProgress } from "@/lib/progress/use-progress";
import { recordPlacement } from "@/lib/progress/store";
import { levelStats } from "@/lib/progress/session";
import { LEVELS, formatScoreBand, getLevel, isMaxLevel } from "@/lib/content/levels";
import { contentCounts } from "@/lib/content/index";
import {
  QUESTIONS_PER_LEVEL,
  buildPlacementTest,
  resolvePlacementLevel,
} from "@/lib/content/placement";
import { LEVEL_IDS, type LevelId } from "@/lib/content/types";
import { seedFrom } from "@/lib/util/shuffle";
import { cn } from "@/lib/util/cn";

export default function LevelsPage() {
  return (
    <AppShell>
      <Levels />
    </AppShell>
  );
}

function Levels() {
  const { user, setLevel } = useAuth();
  const { progress } = useProgress();
  const [testing, setTesting] = useState(false);

  if (!user || !progress) {
    return <p className="text-sm text-muted">読み込み中…</p>;
  }

  if (testing) {
    return (
      <PlacementTest
        seed={seedFrom(`${user.id}-${progress.placement?.completedAt ?? "first"}`)}
        onCancel={() => setTesting(false)}
        onComplete={(level, correct, total) => {
          void setLevel(level);
          setTesting(false);
          return { level, correct, total };
        }}
      />
    );
  }

  const now = new Date();

  return (
    <div className="space-y-8">
      <SectionTitle
        title="レベル別モード"
        description="CEFR A1 から C2 まで。最上位は TOEIC 990 / TOEFL 120 の満点レンジです。"
      />

      <Card>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="font-medium">
              {user.level === null
                ? "レベル未判定"
                : `現在のレベル: L${user.level} ${getLevel(user.level).name}`}
            </p>
            <p className="mt-1 text-sm text-muted">
              {progress.placement
                ? `前回の判定: ${progress.placement.correct}/${progress.placement.total} 正解（${new Date(progress.placement.completedAt).toLocaleDateString("ja-JP")}）`
                : `全 6 レベルから ${QUESTIONS_PER_LEVEL} 問ずつ、計 ${LEVEL_IDS.length * QUESTIONS_PER_LEVEL} 問で判定します。`}
            </p>
          </div>
          <button type="button" onClick={() => setTesting(true)} className="btn-primary shrink-0">
            <Target className="h-4 w-4" aria-hidden />
            {progress.placement ? "判定をやり直す" : "レベル判定を受ける"}
          </button>
        </div>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2">
        {LEVELS.map((definition) => {
          const stats = levelStats(progress, definition.id, now);
          const counts = contentCounts(definition.id);
          const selected = user.level === definition.id;
          return (
            <button
              key={definition.id}
              type="button"
              onClick={() => void setLevel(definition.id)}
              className={cn(
                "card p-5 text-left transition-colors",
                selected ? "ring-2 ring-accent" : "hover:bg-surface-raised",
              )}
              aria-pressed={selected}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="flex items-center gap-2 font-semibold">
                    <span
                      className="inline-block h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: `rgb(${definition.accent})` }}
                      aria-hidden
                    />
                    L{definition.id} {definition.name}
                    {isMaxLevel(definition.id) ? (
                      <Award className="h-4 w-4 text-warning" aria-label="満点レンジ" />
                    ) : null}
                  </p>
                  <p className="mt-1 text-xs text-muted">
                    CEFR {definition.cefr}・{formatScoreBand(definition)}
                  </p>
                </div>
                {selected ? <Check className="h-4 w-4 shrink-0 text-accent" aria-hidden /> : null}
              </div>

              <p className="reading mt-3 text-sm">{definition.canDo}</p>

              <div className="mt-4">
                <ProgressBar
                  value={stats.completion}
                  accent={definition.accent}
                  label={`L${definition.id} の到達度`}
                />
                <p className="mt-2 text-xs text-muted">
                  定着 {stats.settled}/{stats.total}・復習待ち {stats.due}・パッセージ{" "}
                  {counts.passages} 本・目安語彙{" "}
                  {definition.targetVocabulary.toLocaleString("en-US")}
                </p>
              </div>
            </button>
          );
        })}
      </div>

      <Banner tone="info">
        レベルはいつでも手で切り替えられます。難しすぎると感じたら下げ、7
        割以上を安定して取れるなら上げてください。
        判定は「いま解ける最高レベル」ではなく、その一つ上を開始点として返します。
      </Banner>
    </div>
  );
}

function PlacementTest({
  seed,
  onCancel,
  onComplete,
}: {
  seed: number;
  onCancel(): void;
  onComplete(level: LevelId, correct: number, total: number): void;
}) {
  const { update } = useProgress();
  const questions = useMemo(() => buildPlacementTest(seed), [seed]);
  const [index, setIndex] = useState(0);
  const [results, setResults] = useState<readonly boolean[]>([]);
  const [finished, setFinished] = useState<{ level: LevelId; correct: number } | null>(null);

  const answer = useCallback(
    (optionIndex: number) => {
      const question = questions[index];
      if (!question) {
        return;
      }
      const next = [...results, optionIndex === question.answerIndex];
      setResults(next);
      if (index + 1 < questions.length) {
        setIndex(index + 1);
        return;
      }
      const level = resolvePlacementLevel(questions, next);
      const correct = next.filter(Boolean).length;
      update((state, now) =>
        recordPlacement(
          state,
          { completedAt: now.toISOString(), level, correct, total: questions.length },
          now,
        ),
      );
      setFinished({ level, correct });
    },
    [questions, index, results, update],
  );

  if (finished) {
    const definition = getLevel(finished.level);
    return (
      <div className="space-y-6">
        <SectionTitle title="判定結果" />
        <Card>
          <p className="text-sm text-muted">
            {finished.correct}/{questions.length} 正解
          </p>
          <p className="mt-2 text-2xl font-semibold tracking-tight">
            L{definition.id} {definition.name}
          </p>
          <p className="mt-1 text-sm text-muted">
            CEFR {definition.cefr}・{formatScoreBand(definition)}
          </p>
          <p className="reading mt-4 text-sm">{definition.canDo}</p>
          <button
            type="button"
            onClick={() => onComplete(finished.level, finished.correct, questions.length)}
            className="btn-primary mt-5 w-full"
          >
            このレベルで始める
          </button>
        </Card>
      </div>
    );
  }

  const question = questions[index];
  if (!question) {
    return <p className="text-sm text-muted">問題を準備しています…</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <SectionTitle title="レベル判定" description={`${index + 1} / ${questions.length}`} />
        <button
          type="button"
          onClick={onCancel}
          className="text-sm text-muted transition-colors hover:text-fg"
        >
          中断
        </button>
      </div>
      <ProgressBar value={index / questions.length} label="判定の進捗" />

      <Card className="animate-fade-in">
        <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted">
          <Lock className="h-3 w-3" aria-hidden />
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
        <div className="mt-5 space-y-2">
          {question.options.map((option, optionIndex) => (
            <button
              key={option}
              type="button"
              onClick={() => answer(optionIndex)}
              className="w-full rounded-xl border border-border bg-surface px-4 py-3 text-left text-sm transition-colors hover:bg-surface-raised"
            >
              {option}
            </button>
          ))}
        </div>
        <p className="mt-4 text-xs text-muted">
          判定中は正誤を表示しません。分からない問題は当てずに、最も近いと思うものを選んでください。
        </p>
      </Card>
    </div>
  );
}

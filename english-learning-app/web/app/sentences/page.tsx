"use client";

import { useCallback, useMemo, useState } from "react";
import { ArrowLeft, Check, Pause, Play, RotateCcw, X } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Banner, Card, EmptyState, ProgressBar, SectionTitle } from "@/components/ui";
import { useAuth } from "@/lib/auth/context";
import { useProgress } from "@/lib/progress/use-progress";
import { recordPassage } from "@/lib/progress/store";
import { passagesForLevel } from "@/lib/content/index";
import { getLevel } from "@/lib/content/levels";
import { isSpeechSupported, speak, stopSpeaking } from "@/lib/tts/speak";
import { createRandom, seedFrom, shuffle } from "@/lib/util/shuffle";
import type { PassageEntry } from "@/lib/content/types";
import { cn } from "@/lib/util/cn";

export default function SentencesPage() {
  return (
    <AppShell>
      <SentenceTrainer />
    </AppShell>
  );
}

function SentenceTrainer() {
  const { user } = useAuth();
  const { progress } = useProgress();
  const [openId, setOpenId] = useState<string | null>(null);

  const level = user?.level ?? null;

  if (!user || !progress) {
    return <p className="text-sm text-muted">読み込み中…</p>;
  }
  if (level === null) {
    return (
      <EmptyState
        title="先にレベルを決めてください"
        description="レベル別モードのプレースメントを受けると、読むパッセージの難度が決まります。"
      />
    );
  }

  const definition = getLevel(level);
  const passages = passagesForLevel(level);
  const open = passages.find((passage) => passage.id === openId) ?? null;

  if (open) {
    return <PassageReader passage={open} onClose={() => setOpenId(null)} />;
  }

  return (
    <div className="space-y-6">
      <SectionTitle
        title="文章モード"
        description={`L${level} ${definition.name}・${passages.length} 本`}
      />
      {passages.length === 0 ? (
        <EmptyState
          title="このレベルのパッセージはまだありません"
          description="レベルを変えるか、単語モードで語彙を積み上げてください。"
        />
      ) : (
        <div className="space-y-3">
          {passages.map((passage) => {
            const done = progress.passages[passage.id];
            return (
              <button
                key={passage.id}
                type="button"
                onClick={() => setOpenId(passage.id)}
                className="card w-full p-5 text-left transition-colors hover:bg-surface-raised"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="font-medium">{passage.title}</p>
                    <p className="mt-1 text-sm text-muted">
                      {passage.genre}・{passage.clozes.length} blanks・
                      {passage.questions.length} questions
                    </p>
                  </div>
                  {done ? (
                    <span className="chip shrink-0 text-success">
                      <Check className="h-3.5 w-3.5" aria-hidden />
                      {Math.round(done.accuracy * 100)}%
                    </span>
                  ) : null}
                </div>
              </button>
            );
          })}
        </div>
      )}
      <Banner tone="info">
        本文中の空所は、前後の文脈から決まる語を選びます。訳を思い浮かべずに、そこに収まる語を探してください。
      </Banner>
    </div>
  );
}

type Phase = "read" | "cloze" | "questions" | "done";

function PassageReader({ passage, onClose }: { passage: PassageEntry; onClose(): void }) {
  const { update } = useProgress();
  const [phase, setPhase] = useState<Phase>("read");
  const [clozeAnswers, setClozeAnswers] = useState<Record<string, string>>({});
  const [questionAnswers, setQuestionAnswers] = useState<Record<string, number>>({});
  const [speaking, setSpeaking] = useState(false);
  const [rate, setRate] = useState(1);
  const [startedAt] = useState(() => Date.now());

  // 選択肢の並びはパッセージ ID から決まるので、再描画で入れ替わらない。
  const clozeOptions = useMemo(() => {
    const random = createRandom(seedFrom(passage.id));
    return Object.fromEntries(
      passage.clozes.map((cloze) => [cloze.id, shuffle(cloze.options, random)]),
    );
  }, [passage]);

  const questionOptions = useMemo(() => passage.questions, [passage]);

  /** 読み上げ用の平文。空所は正解で埋める（音として自然な文にする）。 */
  const spokenText = useMemo(() => {
    let text = passage.text;
    passage.clozes.forEach((cloze, index) => {
      text = text.replace(`{{${index + 1}}}`, cloze.answer);
    });
    return text;
  }, [passage]);

  const toggleSpeech = useCallback(() => {
    if (speaking) {
      stopSpeaking();
      setSpeaking(false);
      return;
    }
    speak(spokenText, { rate });
    setSpeaking(true);
  }, [speaking, spokenText, rate]);

  const clozeCorrect = passage.clozes.filter(
    (cloze) => clozeAnswers[cloze.id] === cloze.answer,
  ).length;
  const questionCorrect = passage.questions.filter(
    (question) => questionAnswers[question.id] === question.answerIndex,
  ).length;
  const totalItems = passage.clozes.length + passage.questions.length;
  const accuracy = totalItems === 0 ? 0 : (clozeCorrect + questionCorrect) / totalItems;

  const finish = useCallback(() => {
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    update((state, now) => recordPassage(state, passage.id, accuracy, now, seconds));
    stopSpeaking();
    setSpeaking(false);
    setPhase("done");
  }, [update, passage.id, accuracy, startedAt]);

  const paragraphs = passage.text.split("\n\n");
  const answeredClozes = Object.keys(clozeAnswers).length;
  const answeredQuestions = Object.keys(questionAnswers).length;

  return (
    <div className="space-y-6">
      <button
        type="button"
        onClick={() => {
          stopSpeaking();
          onClose();
        }}
        className="flex items-center gap-1.5 text-sm text-muted transition-colors hover:text-fg"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
        一覧へ戻る
      </button>

      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{passage.title}</h1>
        <p className="mt-1 text-sm text-muted">
          {passage.genre}・{passage.clozes.length} blanks・{passage.questions.length} questions
        </p>
      </div>

      {isSpeechSupported() ? (
        <Card className="flex flex-wrap items-center gap-4">
          <button type="button" onClick={toggleSpeech} className="btn-ghost">
            {speaking ? (
              <Pause className="h-4 w-4" aria-hidden />
            ) : (
              <Play className="h-4 w-4" aria-hidden />
            )}
            {speaking ? "Stop" : "Listen"}
          </button>
          <label className="flex items-center gap-2 text-sm text-muted">
            Speed
            <input
              type="range"
              min={0.6}
              max={1.2}
              step={0.1}
              value={rate}
              onChange={(event) => setRate(Number(event.target.value))}
              className="w-28 accent-accent"
              aria-label="読み上げ速度"
            />
            <span className="w-8 tabular-nums">{rate.toFixed(1)}x</span>
          </label>
          <p className="text-xs text-muted">
            速度を落として、聞こえたまま声に出すと定着が早まります。
          </p>
        </Card>
      ) : null}

      <Card>
        <div className="reading">
          {paragraphs.map((paragraph, paragraphIndex) => (
            <p key={paragraphIndex}>{renderParagraph(paragraph, passage, phase, clozeAnswers)}</p>
          ))}
        </div>
      </Card>

      {passage.glossary?.length ? (
        <Card>
          <SectionTitle title="Glossary" description="本文中の語を英語のまま補足します。" />
          <dl className="space-y-2 text-sm">
            {passage.glossary.map((item) => (
              <div key={item.headword} className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
                <dt className="shrink-0 font-medium sm:w-40">{item.headword}</dt>
                <dd className="text-muted">{item.definition}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ) : null}

      {phase === "read" ? (
        <button type="button" onClick={() => setPhase("cloze")} className="btn-primary w-full">
          空所を埋める
        </button>
      ) : null}

      {phase === "cloze" ? (
        <Card>
          <SectionTitle
            title="Fill the blanks"
            description={`${answeredClozes}/${passage.clozes.length} 解答済み`}
          />
          <ProgressBar value={answeredClozes / Math.max(1, passage.clozes.length)} />
          <div className="mt-5 space-y-6">
            {passage.clozes.map((cloze, clozeIndex) => {
              const picked = clozeAnswers[cloze.id];
              return (
                <div key={cloze.id}>
                  <p className="text-sm font-medium">
                    Blank {clozeIndex + 1}
                    <span className="ml-2 font-normal text-muted">{cloze.hint}</span>
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {(clozeOptions[cloze.id] ?? cloze.options).map((option) => {
                      const isPicked = picked === option;
                      const isAnswer = option === cloze.answer;
                      const revealed = picked !== undefined;
                      return (
                        <button
                          key={option}
                          type="button"
                          disabled={revealed}
                          onClick={() =>
                            setClozeAnswers((current) => ({ ...current, [cloze.id]: option }))
                          }
                          className={cn(
                            "rounded-xl border px-3.5 py-2 text-sm transition-colors",
                            !revealed && "border-border bg-surface hover:bg-surface-raised",
                            revealed && isAnswer && "border-success/50 bg-success/10",
                            revealed && isPicked && !isAnswer && "border-danger/50 bg-danger/10",
                            revealed && !isAnswer && !isPicked && "border-border opacity-50",
                          )}
                        >
                          {option}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
          {answeredClozes === passage.clozes.length ? (
            <button
              type="button"
              onClick={() => setPhase("questions")}
              className="btn-primary mt-6 w-full"
            >
              読解設問へ
            </button>
          ) : null}
        </Card>
      ) : null}

      {phase === "questions" ? (
        <Card>
          <SectionTitle
            title="Comprehension"
            description={`${answeredQuestions}/${passage.questions.length} 解答済み`}
          />
          <ProgressBar value={answeredQuestions / Math.max(1, passage.questions.length)} />
          <div className="mt-5 space-y-7">
            {questionOptions.map((question) => {
              const picked = questionAnswers[question.id];
              const revealed = picked !== undefined;
              return (
                <div key={question.id}>
                  <p className="font-medium">{question.prompt}</p>
                  <div className="mt-2.5 space-y-2">
                    {question.options.map((option, optionIndex) => {
                      const isAnswer = optionIndex === question.answerIndex;
                      const isPicked = picked === optionIndex;
                      return (
                        <button
                          key={option}
                          type="button"
                          disabled={revealed}
                          onClick={() =>
                            setQuestionAnswers((current) => ({
                              ...current,
                              [question.id]: optionIndex,
                            }))
                          }
                          className={cn(
                            "flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                            !revealed && "border-border bg-surface hover:bg-surface-raised",
                            revealed && isAnswer && "border-success/50 bg-success/10",
                            revealed && isPicked && !isAnswer && "border-danger/50 bg-danger/10",
                            revealed && !isAnswer && !isPicked && "border-border opacity-50",
                          )}
                        >
                          {revealed && isAnswer ? (
                            <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
                          ) : null}
                          {revealed && isPicked && !isAnswer ? (
                            <X className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden />
                          ) : null}
                          <span>{option}</span>
                        </button>
                      );
                    })}
                  </div>
                  {revealed ? (
                    <p className="mt-2.5 rounded-xl border border-border bg-surface-raised px-3.5 py-2.5 text-sm text-muted">
                      {question.rationale}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
          {answeredQuestions === passage.questions.length ? (
            <button type="button" onClick={finish} className="btn-primary mt-6 w-full">
              結果を記録する
            </button>
          ) : null}
        </Card>
      ) : null}

      {phase === "done" ? (
        <Card>
          <p className="font-medium">記録しました。</p>
          <p className="mt-1.5 text-sm text-muted">
            空所 {clozeCorrect}/{passage.clozes.length}・読解 {questionCorrect}/
            {passage.questions.length}（正答率 {Math.round(accuracy * 100)}%）。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                setClozeAnswers({});
                setQuestionAnswers({});
                setPhase("read");
              }}
              className="btn-ghost"
            >
              <RotateCcw className="h-4 w-4" aria-hidden />
              もう一度読む
            </button>
            <button type="button" onClick={onClose} className="btn-primary">
              一覧へ戻る
            </button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

/**
 * 段落を描画する。読解フェーズ前は空所を下線で示し、解答後は選んだ語を差し込む。
 * 本文と設問で別々に文字列を持つと必ずずれるので、同じ text から両方を作る。
 */
function renderParagraph(
  paragraph: string,
  passage: PassageEntry,
  phase: Phase,
  answers: Record<string, string>,
): React.ReactNode[] {
  const parts = paragraph.split(/(\{\{\d+\}\})/g);
  return parts.map((part, partIndex) => {
    const match = /^\{\{(\d+)\}\}$/.exec(part);
    if (!match?.[1]) {
      return <span key={partIndex}>{part}</span>;
    }
    const cloze = passage.clozes[Number(match[1]) - 1];
    if (!cloze) {
      return <span key={partIndex}>{part}</span>;
    }
    const picked = answers[cloze.id];
    if (picked === undefined) {
      return (
        <span
          key={partIndex}
          className="mx-0.5 inline-block min-w-[5rem] border-b-2 border-dashed border-accent/60 text-center align-baseline text-muted"
        >
          {phase === "read" ? " " : `${Number(match[1])}`}
        </span>
      );
    }
    const correct = picked === cloze.answer;
    return (
      <span
        key={partIndex}
        className={cn(
          "mx-0.5 rounded px-1.5 py-0.5 font-medium",
          correct ? "bg-success/15 text-fg" : "bg-danger/15 text-fg line-through decoration-danger",
        )}
      >
        {picked}
      </span>
    );
  });
}

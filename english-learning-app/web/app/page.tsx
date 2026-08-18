"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ArrowRight, BookOpen, Flame, GraduationCap, Target, Type } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Banner, Card, ProgressBar, SectionTitle, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth/context";
import { useProgress } from "@/lib/progress/use-progress";
import { currentStreak, todayRecord } from "@/lib/progress/store";
import { levelStats, planSession } from "@/lib/progress/session";
import { formatScoreBand, getLevel, isMaxLevel } from "@/lib/content/levels";
import { contentCounts } from "@/lib/content/index";
import { seedFrom } from "@/lib/util/shuffle";

export default function DashboardPage() {
  return (
    <AppShell>
      <Dashboard />
    </AppShell>
  );
}

function Dashboard() {
  const { user } = useAuth();
  const { progress } = useProgress();

  const view = useMemo(() => {
    if (!user || !progress) {
      return null;
    }
    const now = new Date();
    const level = user.level;
    if (level === null) {
      return { placed: false as const, now };
    }
    const stats = levelStats(progress, level, now);
    const plan = planSession(progress, level, progress.dailyGoal, now, seedFrom(user.id));
    const today = todayRecord(progress.daily, now);
    return {
      placed: true as const,
      now,
      level,
      stats,
      plan,
      today,
      streak: currentStreak(progress.daily, now),
      goal: progress.dailyGoal,
    };
  }, [user, progress]);

  if (!user || !view) {
    return <p className="text-sm text-muted">読み込み中…</p>;
  }

  if (!view.placed) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">ようこそ、{user.displayName}</h1>
          <p className="mt-1.5 text-sm text-muted">
            まずレベルを決めます。24 問のプレースメントで、いま解ける範囲の一つ上を開始点にします。
          </p>
        </div>
        <Card>
          <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="font-medium">レベル判定を受ける</p>
              <p className="mt-1 text-sm text-muted">
                所要 5 分ほど。あとから何度でもやり直せます。
              </p>
            </div>
            <Link href="/levels" className="btn-primary shrink-0">
              はじめる
              <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          </div>
        </Card>
      </div>
    );
  }

  const definition = getLevel(view.level);
  const counts = contentCounts(view.level);
  const goalRatio = view.goal === 0 ? 0 : view.today.wordsReviewed / view.goal;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">ようこそ、{user.displayName}</h1>
        <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-muted">
          <span className="chip" style={{ color: `rgb(${definition.accent})` }}>
            L{definition.id} {definition.name}
          </span>
          <span>
            CEFR {definition.cefr}・{formatScoreBand(definition)}
            {isMaxLevel(definition.id) ? "（満点レンジ）" : ""}
          </span>
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          label="今日の単語"
          value={`${view.today.wordsReviewed}/${view.goal}`}
          hint={`正答 ${view.today.wordsCorrect}`}
        />
        <Stat label="連続学習" value={`${view.streak} 日`} />
        <Stat label="復習待ち" value={view.plan.dueCount} hint={`新規 ${view.plan.newCount}`} />
        <Stat
          label="レベル到達度"
          value={`${Math.round(view.stats.completion * 100)}%`}
          hint={`定着 ${view.stats.settled}/${view.stats.total}`}
        />
      </div>

      <Card>
        <SectionTitle title="今日の目標" description={`1 日 ${view.goal} 語を目安にしています。`} />
        <ProgressBar value={goalRatio} accent={definition.accent} label="今日の目標達成率" />
        <p className="mt-3 text-sm text-muted">
          {view.plan.items.length === 0
            ? "このレベルで今日出せる語はありません。文章モードへ進むか、レベルを上げてください。"
            : `残り ${view.plan.items.length} 語（復習 ${view.plan.dueCount}・新規 ${view.plan.newCount}）。`}
        </p>
      </Card>

      <div>
        <SectionTitle
          title="学習モード"
          description="どこから始めても進捗は同じ記録へ積み上がります。"
        />
        <div className="grid gap-3 sm:grid-cols-3">
          <ModeCard
            href="/words"
            Icon={Type}
            title="単語モード"
            description="英英定義と用例だけで意味を掴み、間隔反復で定着させます。"
            meta={`収録 ${counts.words} 語`}
          />
          <ModeCard
            href="/sentences"
            Icon={BookOpen}
            title="文章モード"
            description="英文を読み、穴埋めと読解で理解を確かめます。音読も再生できます。"
            meta={`収録 ${counts.passages} 本`}
          />
          <ModeCard
            href="/levels"
            Icon={GraduationCap}
            title="レベル別モード"
            description="A1 から C2（TOEIC 990 / TOEFL 120）まで、レベルを選んで進みます。"
            meta="全 6 レベル"
          />
        </div>
      </div>

      <Banner tone="info">
        <div className="flex items-start gap-2.5">
          <Target className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <p>
            このアプリは日本語訳を出しません。分からない語は、定義・用例・前後の文から掴んでください。
            訳を挟まずに読めた回数が増えるほど、読む速度が上がります。
          </p>
        </div>
      </Banner>

      {view.streak >= 3 ? (
        <Banner tone="success">
          <div className="flex items-center gap-2.5">
            <Flame className="h-4 w-4 shrink-0" aria-hidden />
            <p>{view.streak} 日続いています。</p>
          </div>
        </Banner>
      ) : null}
    </div>
  );
}

function ModeCard({
  href,
  Icon,
  title,
  description,
  meta,
}: {
  href: string;
  Icon: typeof Type;
  title: string;
  description: string;
  meta: string;
}) {
  return (
    <Link href={href} className="card group p-5 transition-colors hover:bg-surface-raised">
      <Icon className="h-5 w-5 text-accent" aria-hidden />
      <p className="mt-3 font-medium">{title}</p>
      <p className="mt-1.5 text-sm leading-relaxed text-muted">{description}</p>
      <p className="mt-3 text-xs text-muted">{meta}</p>
    </Link>
  );
}

"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Save } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Banner, Card, SectionTitle } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAuth } from "@/lib/auth/context";
import { useProgress } from "@/lib/progress/use-progress";
import { setDailyGoal } from "@/lib/progress/store";
import { isPersistentStorageAvailable } from "@/lib/storage/kv";
import { MIN_PASSWORD_LENGTH, type AuthFailure } from "@/lib/auth/types";

const MESSAGES: Record<AuthFailure, string> = {
  "invalid-credentials": "現在のパスワードが違います。",
  "username-taken": "そのユーザー名は既に使われています。",
  "weak-password": `新しいパスワードは ${MIN_PASSWORD_LENGTH} 文字以上にしてください。`,
  "not-found": "アカウントが見つかりません。",
  forbidden: "この操作を行う権限がありません。",
  "storage-unavailable": "この端末ではデータを保存できません。",
};

export default function SettingsPage() {
  return (
    <AppShell>
      <Settings />
    </AppShell>
  );
}

function Settings() {
  const { user, changePassword } = useAuth();
  const { progress, update } = useProgress();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [goal, setGoal] = useState(20);
  const [storageOk, setStorageOk] = useState(true);

  useEffect(() => {
    setStorageOk(isPersistentStorageAvailable());
  }, []);

  useEffect(() => {
    if (progress) {
      setGoal(progress.dailyGoal);
    }
  }, [progress]);

  if (!user) {
    return <p className="text-sm text-muted">読み込み中…</p>;
  }

  async function handlePassword(event: React.FormEvent) {
    event.preventDefault();
    setMessage(null);
    if (next !== confirm) {
      setMessage({ tone: "danger", text: "新しいパスワードが一致しません。" });
      return;
    }
    const result = await changePassword(current, next);
    if (!result.ok) {
      setMessage({ tone: "danger", text: MESSAGES[result.reason] });
      return;
    }
    setCurrent("");
    setNext("");
    setConfirm("");
    setMessage({ tone: "success", text: "パスワードを変更しました。" });
  }

  return (
    <div className="space-y-8">
      <SectionTitle title="設定" description={`${user.displayName}（${user.username}）`} />

      {!storageOk ? (
        <Banner tone="danger">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <p>
              この端末では学習記録を保存できません。プライベートブラウジングを解除するか、
              別のブラウザで開いてください。解答は記録されずに失われます。
            </p>
          </div>
        </Banner>
      ) : null}

      {user.mustChangePassword ? (
        <Banner tone="warning">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <p>初期パスワードのままです。公開値なので、変更するまで学習画面へは進めません。</p>
          </div>
        </Banner>
      ) : null}

      <Card>
        <SectionTitle title="配色" description="既定は OS の設定に追従します。" />
        <ThemeToggle />
      </Card>

      <Card>
        <SectionTitle
          title="1 日の目標"
          description="単語モードで 1 セッションに出す語数の上限です。"
        />
        <div className="flex flex-wrap items-center gap-4">
          <input
            type="range"
            min={5}
            max={100}
            step={5}
            value={goal}
            onChange={(event) => setGoal(Number(event.target.value))}
            onPointerUp={() => update((state, now) => setDailyGoal(state, goal, now))}
            onKeyUp={() => update((state, now) => setDailyGoal(state, goal, now))}
            className="w-56 accent-accent"
            aria-label="1 日の目標語数"
          />
          <span className="text-lg font-semibold tabular-nums">{goal} 語</span>
        </div>
        <p className="mt-3 text-sm text-muted">
          復習が目標数を超える日は新規語を足しません。復習の遅れを翌日へ積み上げないためです。
        </p>
      </Card>

      <Card>
        <SectionTitle
          title="パスワード変更"
          description={`${MIN_PASSWORD_LENGTH} 文字以上。この端末のブラウザ内でハッシュ化して保存します。`}
        />
        <form onSubmit={handlePassword} className="space-y-4">
          <div>
            <label htmlFor="current" className="mb-1.5 block text-sm font-medium">
              現在のパスワード
            </label>
            <input
              id="current"
              type="password"
              autoComplete="current-password"
              required
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              className="field"
            />
          </div>
          <div>
            <label htmlFor="next" className="mb-1.5 block text-sm font-medium">
              新しいパスワード
            </label>
            <input
              id="next"
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              value={next}
              onChange={(event) => setNext(event.target.value)}
              className="field"
            />
          </div>
          <div>
            <label htmlFor="confirm" className="mb-1.5 block text-sm font-medium">
              新しいパスワード（確認）
            </label>
            <input
              id="confirm"
              type="password"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              className="field"
            />
          </div>
          {message ? <Banner tone={message.tone}>{message.text}</Banner> : null}
          <button type="submit" className="btn-primary">
            <Save className="h-4 w-4" aria-hidden />
            変更する
          </button>
        </form>
      </Card>

      <Banner tone="info">
        認証と学習記録はこの端末のブラウザ内だけに保存されます。別の端末へは引き継がれません。
        端末を変える場合は、移行先で改めてレベル判定から始めてください。
      </Banner>
    </div>
  );
}

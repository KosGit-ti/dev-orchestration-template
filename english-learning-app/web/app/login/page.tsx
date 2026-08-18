"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, Loader2 } from "lucide-react";
import { useAuth } from "@/lib/auth/context";
import { SEED_ACCOUNT_HINTS } from "@/lib/auth/local-provider";
import { ThemeToggle } from "@/components/theme-toggle";
import { Banner } from "@/components/ui";
import type { AuthFailure } from "@/lib/auth/types";

const MESSAGES: Record<AuthFailure, string> = {
  "invalid-credentials": "ユーザー名またはパスワードが違います。",
  "username-taken": "そのユーザー名は既に使われています。",
  "weak-password": "パスワードが短すぎます。",
  "not-found": "アカウントが見つかりません。",
  forbidden: "この操作を行う権限がありません。",
  "storage-unavailable":
    "この端末ではデータを保存できません。プライベートブラウジングを解除して開き直してください。",
};

export default function LoginPage() {
  const { loading, user, signIn } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && user) {
      router.replace(user.mustChangePassword ? "/settings" : "/");
    }
  }, [loading, user, router]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const result = await signIn(username, password);
    setSubmitting(false);
    if (!result.ok) {
      setError(MESSAGES[result.reason]);
      return;
    }
    router.replace(result.value.mustChangePassword ? "/settings" : "/");
  }

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>

      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">English in English</h1>
          <p className="mt-2 text-sm text-muted">
            英語を訳さずに理解する練習を、単語・文章・レベル別の 3 つで積み上げます。
          </p>
        </div>

        <form onSubmit={handleSubmit} className="card space-y-4 p-6">
          <div>
            <label htmlFor="username" className="mb-1.5 block text-sm font-medium">
              ユーザー名
            </label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="field"
              placeholder="admin"
            />
          </div>

          <div>
            <label htmlFor="password" className="mb-1.5 block text-sm font-medium">
              パスワード
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="field"
            />
          </div>

          {error ? <Banner tone="danger">{error}</Banner> : null}

          <button type="submit" disabled={submitting} className="btn-primary w-full">
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <KeyRound className="h-4 w-4" aria-hidden />
            )}
            ログイン
          </button>
        </form>

        <div className="mt-6 space-y-3">
          <Banner tone="warning">
            <p className="font-medium">初期アカウント</p>
            <ul className="mt-1.5 space-y-1 font-mono text-xs">
              {SEED_ACCOUNT_HINTS.map((hint) => (
                <li key={hint.username}>
                  {hint.username} / {hint.initialPassword}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs">
              初期パスワードは公開値です。最初のログイン後に変更するまで学習画面へ進めません。
            </p>
          </Banner>
          <p className="px-1 text-xs leading-relaxed text-muted">
            認証と学習記録はこの端末のブラウザ内で完結します。サーバーへは何も送りません。
            端末を共有している場合、他の利用者が同じデータへ触れる点に注意してください。
          </p>
        </div>
      </div>
    </div>
  );
}

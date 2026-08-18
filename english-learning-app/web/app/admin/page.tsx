"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Trash2, UserPlus } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Banner, Card, SectionTitle } from "@/components/ui";
import { useAuth } from "@/lib/auth/context";
import { contentCounts } from "@/lib/content/index";
import { LEVELS, formatScoreBand } from "@/lib/content/levels";
import {
  MIN_PASSWORD_LENGTH,
  type AuthFailure,
  type PublicUser,
  type Role,
} from "@/lib/auth/types";

const MESSAGES: Record<AuthFailure, string> = {
  "invalid-credentials": "認証に失敗しました。",
  "username-taken": "そのユーザー名は既に使われています。",
  "weak-password": `パスワードは ${MIN_PASSWORD_LENGTH} 文字以上にしてください。`,
  "not-found": "アカウントが見つかりません。",
  forbidden: "管理者だけが実行できます。自分自身は削除できません。",
  "storage-unavailable": "この端末ではデータを保存できません。",
};

export default function AdminPage() {
  return (
    <AppShell>
      <Admin />
    </AppShell>
  );
}

function Admin() {
  const { user, provider } = useAuth();
  const [accounts, setAccounts] = useState<readonly PublicUser[] | null>(null);
  const [message, setMessage] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [form, setForm] = useState({
    username: "",
    displayName: "",
    role: "learner" as Role,
    password: "",
  });

  const reload = useCallback(async () => {
    if (!user) {
      return;
    }
    const result = await provider.listAccounts(user.id);
    if (result.ok) {
      setAccounts(result.value);
    } else {
      setMessage({ tone: "danger", text: MESSAGES[result.reason] });
    }
  }, [user, provider]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!user) {
    return <p className="text-sm text-muted">読み込み中…</p>;
  }

  if (user.role !== "admin") {
    return <Banner tone="danger">この画面は管理者だけが開けます。</Banner>;
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!user) {
      return;
    }
    setMessage(null);
    const result = await provider.createAccount(user.id, form);
    if (!result.ok) {
      setMessage({ tone: "danger", text: MESSAGES[result.reason] });
      return;
    }
    setForm({ username: "", displayName: "", role: "learner", password: "" });
    setMessage({
      tone: "success",
      text: `${result.value.username} を作成しました。初回ログイン時にパスワード変更を求めます。`,
    });
    await reload();
  }

  async function handleDelete(target: PublicUser) {
    if (!user) {
      return;
    }
    setMessage(null);
    const result = await provider.deleteAccount(user.id, target.id);
    if (!result.ok) {
      setMessage({ tone: "danger", text: MESSAGES[result.reason] });
      return;
    }
    setMessage({ tone: "success", text: `${target.username} を削除しました。` });
    await reload();
  }

  async function handleReset(target: PublicUser) {
    if (!user) {
      return;
    }
    setMessage(null);
    const temporary = `reset-${target.username}-${Math.floor(Date.now() / 1000)}`;
    const result = await provider.resetPassword(user.id, target.id, temporary);
    if (!result.ok) {
      setMessage({ tone: "danger", text: MESSAGES[result.reason] });
      return;
    }
    setMessage({
      tone: "success",
      text: `${target.username} の仮パスワードは ${temporary} です。本人へ伝え、初回ログインで変更させてください。`,
    });
    await reload();
  }

  return (
    <div className="space-y-8">
      <SectionTitle title="管理" description="アカウントと収録コンテンツを確認します。" />

      {message ? <Banner tone={message.tone}>{message.text}</Banner> : null}

      <Card>
        <SectionTitle title="アカウント" description={`${accounts?.length ?? 0} 件`} />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[36rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="pb-2 font-medium">ユーザー名</th>
                <th className="pb-2 font-medium">表示名</th>
                <th className="pb-2 font-medium">権限</th>
                <th className="pb-2 font-medium">レベル</th>
                <th className="pb-2 font-medium">最終ログイン</th>
                <th className="pb-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {(accounts ?? []).map((account) => (
                <tr key={account.id} className="border-b border-border/60 last:border-0">
                  <td className="py-3 font-medium">{account.username}</td>
                  <td className="py-3 text-muted">{account.displayName}</td>
                  <td className="py-3">
                    <span className="chip">{account.role === "admin" ? "管理者" : "学習者"}</span>
                  </td>
                  <td className="py-3 text-muted">
                    {account.level === null ? "未判定" : `L${account.level}`}
                  </td>
                  <td className="py-3 text-muted">
                    {account.lastLoginAt
                      ? new Date(account.lastLoginAt).toLocaleString("ja-JP")
                      : "—"}
                  </td>
                  <td className="py-3">
                    <div className="flex gap-3">
                      <button
                        type="button"
                        onClick={() => void handleReset(account)}
                        className="text-muted transition-colors hover:text-fg"
                        aria-label={`${account.username} のパスワードを再設定`}
                        title="パスワード再設定"
                      >
                        <KeyRound className="h-4 w-4" aria-hidden />
                      </button>
                      {account.id === user.id ? null : (
                        <button
                          type="button"
                          onClick={() => void handleDelete(account)}
                          className="text-muted transition-colors hover:text-danger"
                          aria-label={`${account.username} を削除`}
                          title="削除"
                        >
                          <Trash2 className="h-4 w-4" aria-hidden />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-muted">
          アカウントはこの端末のブラウザ内にだけ存在します。別の端末には引き継がれません。
        </p>
      </Card>

      <Card>
        <SectionTitle title="アカウント追加" />
        <form onSubmit={handleCreate} className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="new-username" className="mb-1.5 block text-sm font-medium">
              ユーザー名
            </label>
            <input
              id="new-username"
              required
              autoCapitalize="none"
              spellCheck={false}
              value={form.username}
              onChange={(event) => setForm({ ...form, username: event.target.value })}
              className="field"
            />
          </div>
          <div>
            <label htmlFor="new-display" className="mb-1.5 block text-sm font-medium">
              表示名
            </label>
            <input
              id="new-display"
              value={form.displayName}
              onChange={(event) => setForm({ ...form, displayName: event.target.value })}
              className="field"
            />
          </div>
          <div>
            <label htmlFor="new-role" className="mb-1.5 block text-sm font-medium">
              権限
            </label>
            <select
              id="new-role"
              value={form.role}
              onChange={(event) => setForm({ ...form, role: event.target.value as Role })}
              className="field"
            >
              <option value="learner">学習者</option>
              <option value="admin">管理者</option>
            </select>
          </div>
          <div>
            <label htmlFor="new-password" className="mb-1.5 block text-sm font-medium">
              初期パスワード
            </label>
            <input
              id="new-password"
              type="password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value })}
              className="field"
            />
          </div>
          <div className="sm:col-span-2">
            <button type="submit" className="btn-primary">
              <UserPlus className="h-4 w-4" aria-hidden />
              追加する
            </button>
          </div>
        </form>
      </Card>

      <Card>
        <SectionTitle title="収録コンテンツ" description="レベルごとの語数とパッセージ数です。" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[32rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="pb-2 font-medium">レベル</th>
                <th className="pb-2 font-medium">CEFR</th>
                <th className="pb-2 font-medium">スコア帯</th>
                <th className="pb-2 font-medium">語</th>
                <th className="pb-2 font-medium">パッセージ</th>
              </tr>
            </thead>
            <tbody>
              {LEVELS.map((definition) => {
                const counts = contentCounts(definition.id);
                return (
                  <tr key={definition.id} className="border-b border-border/60 last:border-0">
                    <td className="py-2.5 font-medium">
                      L{definition.id} {definition.name}
                    </td>
                    <td className="py-2.5 text-muted">{definition.cefr}</td>
                    <td className="py-2.5 text-muted">{formatScoreBand(definition)}</td>
                    <td className="py-2.5 tabular-nums">{counts.words}</td>
                    <td className="py-2.5 tabular-nums">{counts.passages}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

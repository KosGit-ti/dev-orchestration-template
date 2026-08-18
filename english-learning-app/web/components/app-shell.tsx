"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import {
  BookOpen,
  GraduationCap,
  LayoutDashboard,
  LogOut,
  Settings,
  Shield,
  Type,
} from "lucide-react";
import { useAuth } from "@/lib/auth/context";
import { ThemeToggle } from "./theme-toggle";
import { cn } from "@/lib/util/cn";

interface NavItem {
  readonly href: string;
  readonly label: string;
  readonly Icon: typeof BookOpen;
  readonly adminOnly?: boolean;
}

const NAV: readonly NavItem[] = [
  { href: "/", label: "ホーム", Icon: LayoutDashboard },
  { href: "/words", label: "単語", Icon: Type },
  { href: "/sentences", label: "文章", Icon: BookOpen },
  { href: "/levels", label: "レベル", Icon: GraduationCap },
  { href: "/settings", label: "設定", Icon: Settings },
  { href: "/admin", label: "管理", Icon: Shield, adminOnly: true },
];

/**
 * 認証済み画面の外枠。
 *
 * 未ログイン・初期パスワード未変更の 2 つは、どの画面から入っても同じ場所へ
 * 送る必要がある。個々のページに書くと必ず抜けるので、ここへ一箇所化する。
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { loading, user, signOut } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (loading) {
      return;
    }
    if (!user) {
      router.replace("/login");
      return;
    }
    if (user.mustChangePassword && pathname !== "/settings") {
      router.replace("/settings");
    }
  }, [loading, user, pathname, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <p className="text-sm text-muted">読み込み中…</p>
      </div>
    );
  }

  const items = NAV.filter((item) => !item.adminOnly || user.role === "admin");

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col px-4 pb-24 sm:px-6 lg:flex-row lg:gap-8 lg:pb-8">
      {/* デスクトップ: 左サイドバー */}
      <aside className="hidden shrink-0 py-8 lg:block lg:w-56">
        <Link href="/" className="mb-8 block">
          <span className="text-lg font-semibold tracking-tight">English in English</span>
          <span className="mt-0.5 block text-xs text-muted">訳さずに読む</span>
        </Link>
        <nav className="space-y-1">
          {items.map(({ href, label, Icon }) => (
            <Link
              key={href}
              href={href}
              aria-current={pathname === href ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors",
                pathname === href
                  ? "bg-accent text-accent-fg"
                  : "text-muted hover:bg-surface hover:text-fg",
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
            </Link>
          ))}
        </nav>
        <div className="mt-8 space-y-3 border-t border-border pt-6">
          <ThemeToggle />
          <div className="text-xs text-muted">
            {user.displayName}
            {user.role === "admin" ? "・管理者" : ""}
          </div>
          <button
            type="button"
            onClick={() => void signOut()}
            className="flex items-center gap-2 text-xs text-muted transition-colors hover:text-fg"
          >
            <LogOut className="h-3.5 w-3.5" aria-hidden />
            ログアウト
          </button>
        </div>
      </aside>

      {/* モバイル: 上部ヘッダー */}
      <header className="flex items-center justify-between py-5 lg:hidden">
        <Link href="/" className="text-base font-semibold tracking-tight">
          English in English
        </Link>
        <ThemeToggle />
      </header>

      <main className="min-w-0 flex-1 py-2 lg:py-8">{children}</main>

      {/* モバイル: 下部タブバー。iPhone のホームバーぶんを safe-area で確保する。 */}
      <nav
        className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-surface/95 backdrop-blur lg:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        <div className="mx-auto flex max-w-lg">
          {items.map(({ href, label, Icon }) => (
            <Link
              key={href}
              href={href}
              aria-current={pathname === href ? "page" : undefined}
              className={cn(
                "flex flex-1 flex-col items-center gap-1 py-2.5 text-[0.6875rem] font-medium transition-colors",
                pathname === href ? "text-accent" : "text-muted",
              )}
            >
              <Icon className="h-5 w-5" aria-hidden />
              {label}
            </Link>
          ))}
        </div>
      </nav>
    </div>
  );
}

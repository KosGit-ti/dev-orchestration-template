"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { LocalAuthProvider } from "./local-provider";
import type { AuthProvider, AuthResult, PublicUser } from "./types";
import type { LevelId } from "../content/types";

/**
 * 認証プロバイダは interface 越しにだけ触る。実装差し替え（将来のサーバー同期）
 * が画面へ波及しないようにするため、ここで一度だけ具象を決める。
 */
const provider: AuthProvider = new LocalAuthProvider();

interface AuthState {
  /** 起動時の復元が終わるまで true。この間は画面を確定させない。 */
  readonly loading: boolean;
  readonly user: PublicUser | null;
  signIn(username: string, password: string): Promise<AuthResult<PublicUser>>;
  signOut(): Promise<void>;
  changePassword(current: string, next: string): Promise<AuthResult<PublicUser>>;
  setLevel(level: LevelId): Promise<void>;
  /** 管理画面から一覧などを操作した後に呼び、表示中の利用者情報を取り直す。 */
  refresh(): Promise<void>;
  readonly provider: AuthProvider;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthContextProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<PublicUser | null>(null);

  const refresh = useCallback(async () => {
    setUser(await provider.currentUser());
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await provider.bootstrap();
      const restored = await provider.currentUser();
      if (!cancelled) {
        setUser(restored);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    const result = await provider.signIn(username, password);
    if (result.ok) {
      setUser(result.value);
    }
    return result;
  }, []);

  const signOut = useCallback(async () => {
    await provider.signOut();
    setUser(null);
  }, []);

  const changePassword = useCallback(
    async (current: string, next: string) => {
      if (!user) {
        return { ok: false as const, reason: "not-found" as const };
      }
      const result = await provider.changePassword(user.id, current, next);
      if (result.ok) {
        setUser(result.value);
      }
      return result;
    },
    [user],
  );

  const setLevel = useCallback(
    async (level: LevelId) => {
      if (!user) {
        return;
      }
      const result = await provider.updateLevel(user.id, level);
      if (result.ok) {
        setUser(result.value);
      }
    },
    [user],
  );

  const value = useMemo<AuthState>(
    () => ({ loading, user, signIn, signOut, changePassword, setLevel, refresh, provider }),
    [loading, user, signIn, signOut, changePassword, setLevel, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth は AuthContextProvider の内側でだけ使えます。");
  }
  return context;
}

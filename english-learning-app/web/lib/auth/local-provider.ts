import { generateToken, hashPassword, verifyPassword } from "./crypto";
import {
  isAcceptablePassword,
  type Account,
  type AuthFailure,
  type AuthProvider,
  type AuthResult,
  type PublicUser,
  type Role,
  type Session,
} from "./types";
import {
  isPersistentStorageAvailable,
  readJson,
  removeKey,
  storageKey,
  writeJson,
} from "../storage/kv";
import type { LevelId } from "../content/types";

const ACCOUNTS_KEY = storageKey("auth", "accounts", "v1");
const SESSION_KEY = storageKey("auth", "session", "v1");
const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * 初期アカウント。
 *
 * パスワードは公開された初期値であり、秘密ではない。だから `mustChangePassword`
 * を true にし、変更するまで学習画面へ入れない設計にしてある。ここへ本物の
 * パスワードを書くことは P-002 違反であり、しない。
 */
const SEED_ACCOUNTS: readonly {
  username: string;
  displayName: string;
  role: Role;
  initialPassword: string;
}[] = [
  {
    username: "admin",
    displayName: "Administrator",
    role: "admin",
    initialPassword: "change-me-admin",
  },
  {
    username: "shunsuke",
    displayName: "Shunsuke",
    role: "learner",
    initialPassword: "change-me-shunsuke",
  },
];

function normalizeUsername(username: string): string {
  return username.trim().toLowerCase();
}

function toPublic(account: Account): PublicUser {
  const { password: _password, ...rest } = account;
  return rest;
}

function ok<T>(value: T): AuthResult<T> {
  return { ok: true, value };
}

function fail(reason: AuthFailure): { ok: false; reason: AuthFailure } {
  return { ok: false, reason };
}

/** クライアント完結の認証プロバイダ。localStorage を唯一の保存先とする。 */
export class LocalAuthProvider implements AuthProvider {
  private readAccounts(): Account[] {
    return readJson<Account[]>(ACCOUNTS_KEY) ?? [];
  }

  private writeAccounts(accounts: readonly Account[]): boolean {
    return writeJson(ACCOUNTS_KEY, accounts);
  }

  private findById(id: string): Account | null {
    return this.readAccounts().find((account) => account.id === id) ?? null;
  }

  private readSession(): Session | null {
    const session = readJson<Session>(SESSION_KEY);
    if (!session) {
      return null;
    }
    if (Date.parse(session.expiresAt) <= Date.now()) {
      removeKey(SESSION_KEY);
      return null;
    }
    return session;
  }

  async bootstrap(): Promise<void> {
    if (!isPersistentStorageAvailable()) {
      return;
    }
    const existing = this.readAccounts();
    const known = new Set(existing.map((account) => account.username));
    const missing = SEED_ACCOUNTS.filter((seed) => !known.has(seed.username));
    if (missing.length === 0) {
      return;
    }
    const created: Account[] = [];
    for (const seed of missing) {
      created.push({
        id: `u-${seed.username}`,
        username: seed.username,
        displayName: seed.displayName,
        role: seed.role,
        password: await hashPassword(seed.initialPassword),
        mustChangePassword: true,
        level: null,
        createdAt: new Date().toISOString(),
        lastLoginAt: null,
      });
    }
    this.writeAccounts([...existing, ...created]);
  }

  async signIn(username: string, password: string): Promise<AuthResult<PublicUser>> {
    if (!isPersistentStorageAvailable()) {
      return fail("storage-unavailable");
    }
    const accounts = this.readAccounts();
    const account = accounts.find((item) => item.username === normalizeUsername(username));
    if (!account) {
      // アカウントの有無を応答時間の差から読まれないよう、存在しない場合も
      // 同等の鍵導出コストを払ってから同じ理由を返す。
      await hashPassword(password);
      return fail("invalid-credentials");
    }
    if (!(await verifyPassword(password, account.password))) {
      return fail("invalid-credentials");
    }
    const now = new Date();
    const updated: Account = { ...account, lastLoginAt: now.toISOString() };
    this.writeAccounts(accounts.map((item) => (item.id === account.id ? updated : item)));
    const session: Session = {
      token: generateToken(),
      userId: account.id,
      issuedAt: now.toISOString(),
      expiresAt: new Date(now.getTime() + SESSION_TTL_MS).toISOString(),
    };
    writeJson(SESSION_KEY, session);
    return ok(toPublic(updated));
  }

  async signOut(): Promise<void> {
    removeKey(SESSION_KEY);
  }

  async currentUser(): Promise<PublicUser | null> {
    const session = this.readSession();
    if (!session) {
      return null;
    }
    const account = this.findById(session.userId);
    return account ? toPublic(account) : null;
  }

  async changePassword(
    userId: string,
    currentPassword: string,
    nextPassword: string,
  ): Promise<AuthResult<PublicUser>> {
    if (!isAcceptablePassword(nextPassword)) {
      return fail("weak-password");
    }
    const accounts = this.readAccounts();
    const account = accounts.find((item) => item.id === userId);
    if (!account) {
      return fail("not-found");
    }
    if (!(await verifyPassword(currentPassword, account.password))) {
      return fail("invalid-credentials");
    }
    const updated: Account = {
      ...account,
      password: await hashPassword(nextPassword),
      mustChangePassword: false,
    };
    if (!this.writeAccounts(accounts.map((item) => (item.id === userId ? updated : item)))) {
      return fail("storage-unavailable");
    }
    return ok(toPublic(updated));
  }

  async updateLevel(userId: string, level: LevelId): Promise<AuthResult<PublicUser>> {
    const accounts = this.readAccounts();
    const account = accounts.find((item) => item.id === userId);
    if (!account) {
      return fail("not-found");
    }
    const updated: Account = { ...account, level };
    if (!this.writeAccounts(accounts.map((item) => (item.id === userId ? updated : item)))) {
      return fail("storage-unavailable");
    }
    return ok(toPublic(updated));
  }

  private requireAdmin(actorId: string): Account | null {
    const actor = this.findById(actorId);
    return actor && actor.role === "admin" ? actor : null;
  }

  async listAccounts(actorId: string): Promise<AuthResult<readonly PublicUser[]>> {
    if (!this.requireAdmin(actorId)) {
      return fail("forbidden");
    }
    return ok(this.readAccounts().map(toPublic));
  }

  async createAccount(
    actorId: string,
    input: { username: string; displayName: string; role: Role; password: string },
  ): Promise<AuthResult<PublicUser>> {
    if (!this.requireAdmin(actorId)) {
      return fail("forbidden");
    }
    if (!isAcceptablePassword(input.password)) {
      return fail("weak-password");
    }
    const username = normalizeUsername(input.username);
    const accounts = this.readAccounts();
    if (accounts.some((item) => item.username === username)) {
      return fail("username-taken");
    }
    const account: Account = {
      id: `u-${username}-${generateToken().slice(0, 6)}`,
      username,
      displayName: input.displayName.trim() || username,
      role: input.role,
      password: await hashPassword(input.password),
      mustChangePassword: true,
      level: null,
      createdAt: new Date().toISOString(),
      lastLoginAt: null,
    };
    if (!this.writeAccounts([...accounts, account])) {
      return fail("storage-unavailable");
    }
    return ok(toPublic(account));
  }

  async deleteAccount(actorId: string, targetId: string): Promise<AuthResult<true>> {
    const actor = this.requireAdmin(actorId);
    if (!actor) {
      return fail("forbidden");
    }
    if (actor.id === targetId) {
      // 自分を消すと管理者不在になり復旧手段が無くなる。安全側に倒す（P-010）。
      return fail("forbidden");
    }
    const accounts = this.readAccounts();
    if (!accounts.some((item) => item.id === targetId)) {
      return fail("not-found");
    }
    if (!this.writeAccounts(accounts.filter((item) => item.id !== targetId))) {
      return fail("storage-unavailable");
    }
    return ok(true);
  }

  async resetPassword(
    actorId: string,
    targetId: string,
    nextPassword: string,
  ): Promise<AuthResult<PublicUser>> {
    if (!this.requireAdmin(actorId)) {
      return fail("forbidden");
    }
    if (!isAcceptablePassword(nextPassword)) {
      return fail("weak-password");
    }
    const accounts = this.readAccounts();
    const account = accounts.find((item) => item.id === targetId);
    if (!account) {
      return fail("not-found");
    }
    const updated: Account = {
      ...account,
      password: await hashPassword(nextPassword),
      mustChangePassword: true,
    };
    if (!this.writeAccounts(accounts.map((item) => (item.id === targetId ? updated : item)))) {
      return fail("storage-unavailable");
    }
    return ok(toPublic(updated));
  }
}

/** 初期アカウントの一覧。ログイン画面と README で同じ値を示すために公開する。 */
export const SEED_ACCOUNT_HINTS = SEED_ACCOUNTS.map((seed) => ({
  username: seed.username,
  initialPassword: seed.initialPassword,
  role: seed.role,
}));

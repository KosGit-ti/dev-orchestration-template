import type { PasswordHash } from "./crypto";
import type { LevelId } from "../content/types";

export type Role = "admin" | "learner";

/** 保存されるアカウント。パスワードは常にハッシュだけを持つ。 */
export interface Account {
  readonly id: string;
  /** ログイン名。大文字小文字を区別しない比較のため、保存は小文字へ正規化する。 */
  readonly username: string;
  readonly displayName: string;
  readonly role: Role;
  readonly password: PasswordHash;
  /** 初期パスワードのまま使わせないためのフラグ。true の間は学習画面へ入れない。 */
  readonly mustChangePassword: boolean;
  /** 学習開始レベル。プレースメント未実施なら null。 */
  readonly level: LevelId | null;
  readonly createdAt: string;
  readonly lastLoginAt: string | null;
}

/** 画面へ渡す公開情報。パスワードハッシュを含めない。 */
export type PublicUser = Omit<Account, "password">;

export interface Session {
  readonly token: string;
  readonly userId: string;
  readonly issuedAt: string;
  readonly expiresAt: string;
}

export type AuthFailure =
  | "invalid-credentials"
  | "username-taken"
  | "weak-password"
  | "not-found"
  | "forbidden"
  | "storage-unavailable";

export type AuthResult<T> = { ok: true; value: T } | { ok: false; reason: AuthFailure };

/**
 * 認証プロバイダの契約。
 *
 * 初期版はクライアント完結の実装（LocalAuthProvider）だけを持つ。将来
 * サーバー同期へ移す際は、この interface を満たす別実装を差し込み、
 * 画面側は変更しない。だから戻り値はすべて非同期にしてある。
 */
export interface AuthProvider {
  /** 初回起動時に既定アカウントを用意する。冪等でなければならない。 */
  bootstrap(): Promise<void>;
  signIn(username: string, password: string): Promise<AuthResult<PublicUser>>;
  signOut(): Promise<void>;
  /** 現在のセッションから利用者を復元する。未ログインなら null。 */
  currentUser(): Promise<PublicUser | null>;
  changePassword(
    userId: string,
    currentPassword: string,
    nextPassword: string,
  ): Promise<AuthResult<PublicUser>>;
  updateLevel(userId: string, level: LevelId): Promise<AuthResult<PublicUser>>;
  /** 管理者だけが呼べる。呼び出し元の role を必ず検査する。 */
  listAccounts(actorId: string): Promise<AuthResult<readonly PublicUser[]>>;
  createAccount(
    actorId: string,
    input: { username: string; displayName: string; role: Role; password: string },
  ): Promise<AuthResult<PublicUser>>;
  deleteAccount(actorId: string, targetId: string): Promise<AuthResult<true>>;
  resetPassword(
    actorId: string,
    targetId: string,
    nextPassword: string,
  ): Promise<AuthResult<PublicUser>>;
}

/** パスワードの最低要件。短すぎるものは弾く。 */
export const MIN_PASSWORD_LENGTH = 10;

export function isAcceptablePassword(password: string): boolean {
  return password.trim().length >= MIN_PASSWORD_LENGTH;
}

/**
 * パスワードハッシュ。
 *
 * WebCrypto の PBKDF2-HMAC-SHA256 を使う。反復回数は OWASP の推奨
 * （2023 年時点で 600,000）に合わせる。ブラウザのネイティブ実装で走るため、
 * ログイン 1 回あたりの体感は数百ミリ秒に収まる。
 *
 * 前提の明示: 本方式は「同じ端末を使う他人がストレージを覗いても素の
 * パスワードが読めない」ことを守るだけである。サーバーが無い以上、
 * 攻撃者が端末を掌握した状況を防ぐものではない（docs/policies.md P-102）。
 */

const ITERATIONS = 600_000;
const SALT_BYTES = 16;
const KEY_BITS = 256;

export interface PasswordHash {
  /** アルゴリズム識別子。将来の移行時に古い形式を判別するために持つ。 */
  readonly algorithm: "PBKDF2-SHA256";
  readonly iterations: number;
  /** base64url のソルト。 */
  readonly salt: string;
  /** base64url の導出鍵。 */
  readonly hash: string;
}

function subtle(): SubtleCrypto {
  const c = globalThis.crypto;
  if (!c?.subtle) {
    throw new Error("WebCrypto が利用できません。HTTPS または localhost で開いてください。");
  }
  return c.subtle;
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded.padEnd(Math.ceil(padded.length / 4) * 4, "="));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

async function derive(password: string, salt: Uint8Array, iterations: number): Promise<Uint8Array> {
  const material = await subtle().importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await subtle().deriveBits(
    { name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" },
    material,
    KEY_BITS,
  );
  return new Uint8Array(bits);
}

/** 新しいソルトでパスワードをハッシュ化する。 */
export async function hashPassword(password: string): Promise<PasswordHash> {
  const salt = globalThis.crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const hash = await derive(password, salt, ITERATIONS);
  return {
    algorithm: "PBKDF2-SHA256",
    iterations: ITERATIONS,
    salt: toBase64Url(salt),
    hash: toBase64Url(hash),
  };
}

/**
 * パスワードを検証する。
 * 比較は長さ非依存の定数時間で行い、先頭一致からの推測余地を残さない。
 */
export async function verifyPassword(password: string, stored: PasswordHash): Promise<boolean> {
  const salt = fromBase64Url(stored.salt);
  const expected = fromBase64Url(stored.hash);
  const actual = await derive(password, salt, stored.iterations);
  if (actual.length !== expected.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < actual.length; i += 1) {
    diff |= (actual[i] ?? 0) ^ (expected[i] ?? 0);
  }
  return diff === 0;
}

/** セッショントークン。推測不能な乱数で十分（署名検証する相手が居ない）。 */
export function generateToken(): string {
  return toBase64Url(globalThis.crypto.getRandomValues(new Uint8Array(24)));
}

/**
 * 端末内 key-value ストア。
 *
 * 初期版はサーバーを持たないため localStorage を使う。将来サーバー同期
 * （Supabase 等）へ差し替える際は、この薄い層だけを置き換えれば済むように
 * 読み書きを一箇所へ集める。SSR / 静的エクスポート時は window が無いので、
 * 例外ではなく null を返して呼び出し側が既定値へ倒せるようにする。
 */

/** アプリ全体の名前空間。他アプリと同一オリジンに同居しても衝突しない。 */
const NAMESPACE = "eie";

export function storageKey(...parts: readonly string[]): string {
  return [NAMESPACE, ...parts].join(":");
}

function backend(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    // プライベートブラウジング等で localStorage が例外を投げる環境がある。
    // 学習は続行できたほうがよいので、保存不可として扱う（P-010 の範囲内）。
    return null;
  }
}

/** JSON として読む。未保存・破損時は null を返す。 */
export function readJson<T>(key: string): T | null {
  const store = backend();
  if (!store) {
    return null;
  }
  const raw = store.getItem(key);
  if (raw === null) {
    return null;
  }
  try {
    return JSON.parse(raw) as T;
  } catch {
    // 破損データを黙って上書きすると原因が消えるため、読み出しは失敗扱いにして
    // 呼び出し側の初期化に委ねる。
    return null;
  }
}

/** JSON として書く。保存できない環境では false を返す。 */
export function writeJson(key: string, value: unknown): boolean {
  const store = backend();
  if (!store) {
    return false;
  }
  try {
    store.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    // 容量超過（QuotaExceededError）を含む。
    return false;
  }
}

export function removeKey(key: string): void {
  backend()?.removeItem(key);
}

/** 保存が実際に効く環境かどうか。UI で警告を出すために使う。 */
export function isPersistentStorageAvailable(): boolean {
  const store = backend();
  if (!store) {
    return false;
  }
  const probe = storageKey("probe");
  try {
    store.setItem(probe, "1");
    store.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

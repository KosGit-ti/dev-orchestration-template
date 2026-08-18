import "@testing-library/jest-dom";
import { webcrypto } from "node:crypto";
import { TextDecoder, TextEncoder } from "node:util";

/**
 * jsdom には WebCrypto の subtle と TextEncoder が無い。認証層は PBKDF2 を
 * 使うため、Node の実装を差し込んで本番と同じ経路をテストする。ハッシュ関数を
 * モックへ置き換えると計算そのものが検証されなくなるので、実装を借りる形にする。
 * ブラウザ側にはどちらも標準で存在するため、これはテスト環境だけの補填である。
 */
if (typeof globalThis.TextEncoder === "undefined") {
  Object.assign(globalThis, { TextEncoder, TextDecoder });
}

if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
}

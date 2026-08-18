/* eslint-disable */
/**
 * Service Worker。
 *
 * 目的はオフラインでの学習継続だけである。通知・バックグラウンド同期・
 * プッシュは扱わない。配信元のサブパス（GitHub Pages のプロジェクトサイト）が
 * 変わっても動くよう、パスは registration.scope から相対で組み立てる。
 *
 * キャッシュ方針:
 *   - ページ遷移: network-first。更新をすぐ反映し、オフライン時だけ退避する。
 *   - 静的アセット: stale-while-revalidate。表示は速く、裏で新しくする。
 */
const VERSION = "v1";
const CACHE = `eie-${VERSION}`;
const SCOPE = new URL(self.registration.scope);

const PRECACHE = [
  "./",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
].map((path) => new URL(path, SCOPE).toString());

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      // 1 件でも失敗すると install 全体が落ちるため、個別に握りつぶす。
      .then((cache) => Promise.all(PRECACHE.map((url) => cache.add(url).catch(() => undefined))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() =>
          caches
            .match(request)
            .then((cached) => cached ?? caches.match(new URL("./", SCOPE).toString()))
            .then((cached) => cached ?? Response.error()),
        ),
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached ?? network;
    }),
  );
});

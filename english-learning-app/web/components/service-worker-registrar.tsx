"use client";

import { useEffect } from "react";

/**
 * Service Worker の登録。
 *
 * 目的はオフライン学習であり、通知やバックグラウンド同期は使わない。
 * 開発中は登録しない。古いキャッシュが効いて変更が反映されない事故を防ぐ。
 */
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) {
      return;
    }
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
    void navigator.serviceWorker.register(`${basePath}/sw.js`, { scope: `${basePath}/` });
  }, []);

  return null;
}

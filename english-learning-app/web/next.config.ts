import type { NextConfig } from "next";

/**
 * GitHub Pages のプロジェクトサイトはサブパス配信になるため、
 * basePath / assetPrefix を環境変数で切り替える。
 * ルートドメイン配信（独自ドメイン・Cloudflare Pages 等）では未設定でよい。
 */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const nextConfig: NextConfig = {
  // 静的エクスポート。サーバーを持たないため GitHub Pages へそのまま置ける。
  output: process.env.NEXT_OUTPUT === "export" ? "export" : undefined,
  basePath: basePath || undefined,
  assetPrefix: basePath || undefined,
  // 静的エクスポートでは next/image の最適化サーバーが使えない。
  images: { unoptimized: true },
  // ディレクトリ末尾スラッシュを付け、Pages の静的配信で 404 になりにくくする。
  trailingSlash: true,
  reactStrictMode: true,
};

export default nextConfig;

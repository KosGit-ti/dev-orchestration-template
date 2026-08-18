import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ThemeProvider, THEME_BOOTSTRAP_SCRIPT } from "@/lib/theme/theme-provider";
import { AuthContextProvider } from "@/lib/auth/context";
import { ServiceWorkerRegistrar } from "@/components/service-worker-registrar";

const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export const metadata: Metadata = {
  title: "English in English",
  description: "英語を英語のまま理解して覚えるための学習アプリ",
  manifest: `${basePath}/manifest.webmanifest`,
  appleWebApp: {
    capable: true,
    title: "English in English",
    statusBarStyle: "default",
  },
  icons: {
    icon: `${basePath}/icons/icon-192.png`,
    apple: `${basePath}/icons/apple-touch-icon.png`,
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // 学習中に誤って拡大されるのは煩わしいが、拡大を禁じると弱視の利用者を締め出す。
  // 上限を設けたうえで拡大自体は許可する。
  maximumScale: 5,
  themeColor: "#f6f8fb",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja" suppressHydrationWarning>
      <head>
        {/* 初回描画の配色ちらつきを防ぐため、body より前に同期実行する。 */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>
          <AuthContextProvider>
            {children}
            <ServiceWorkerRegistrar />
          </AuthContextProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

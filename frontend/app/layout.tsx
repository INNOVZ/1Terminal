import type { Metadata } from "next";
import dynamic from "next/dynamic";
import "./globals.css";

const BottomDock = dynamic(() => import("./components/BottomDock"));

export const metadata: Metadata = {
  title: "Atlas Institutional Crypto Portal — Pre-Breakout Radar & Multi-Day Watchlist",
  description:
    "Institutional Wyckoff accumulation screener, Volume Profile value acceptance engine, and multi-day crypto watchlist for Binance USD-M markets.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Manrope:wght@300;400;500;600;700;800&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        {children}
        <BottomDock />
      </body>
    </html>
  );
}

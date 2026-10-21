import type { Metadata } from "next";
import dynamic from "next/dynamic";
import "./globals.css";

const BottomDock = dynamic(() => import("./components/BottomDock"));

export const metadata: Metadata = {
  title: "Alfa Terminal — Crypto Screener | Multi-Day breakout Watchlist",
  description:
    "Institutional crypto screener, Wyckoff accumulation, Volume Profile value acceptance engine, and multi-day crypto watchlist",
  icons: {
    icon: [
      {
        url: "/favicon.svg",
        type: "image/svg+xml",
      },
    ],
    shortcut: "/favicon.svg",
    apple: "/favicon.svg",
  },
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
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
      </head>
      <body>
        {children}
        <BottomDock />
      </body>
    </html>
  );
}

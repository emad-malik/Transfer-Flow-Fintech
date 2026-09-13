import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MeowPay - Send Treats",
  description: "Send treats between cat wallets.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

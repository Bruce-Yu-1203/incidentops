import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "IncidentOps",
  description: "Policy-governed AI incident investigation runtime (lab prototype)",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

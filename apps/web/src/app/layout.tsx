import type { Metadata } from "next";
import { Suspense } from "react";

import "./globals.css";
import { UserBar } from "./user-bar";

export const metadata: Metadata = {
  title: "IncidentOps",
  description: "Policy-governed AI incident investigation runtime (lab prototype)",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Suspense fallback={<nav className="userbar">IncidentOps</nav>}>
          <UserBar />
        </Suspense>
        {children}
      </body>
    </html>
  );
}

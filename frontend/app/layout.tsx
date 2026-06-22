import "../styles/globals.css";
import type { ReactNode } from "react";
import AuthGate from "@/components/AuthGate";
import Shell from "@/components/Shell";

export const metadata = {
  title: "Production-AI",
  description: "Garment manufacturing intelligence",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        <AuthGate>
          <Shell>{children}</Shell>
        </AuthGate>
      </body>
    </html>
  );
}

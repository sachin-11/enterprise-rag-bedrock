import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Nav } from "./components/Nav";
import { AuthProvider } from "./context/AuthProvider";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Enterprise RAG Bedrock",
  description: "Enterprise RAG application powered by AWS Bedrock",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      {/* Nav renders here (not per route group) so it's identical and always
          present — landing page, auth pages, and the protected app all get
          the same chrome, just with different content on the right (auth
          links vs. the logged-in user menu). */}
      <body
        className={`${inter.className} flex h-screen flex-col overflow-hidden bg-gray-50 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(59,130,246,0.08),transparent)] text-gray-900`}
      >
        <AuthProvider>
          <Nav />
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}

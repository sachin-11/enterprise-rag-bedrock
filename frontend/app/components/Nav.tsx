"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "../context/AuthProvider";

const baseLinks = [
  {
    href: "/chat",
    label: "Chat",
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z"
      />
    ),
  },
  {
    href: "/documents",
    label: "Documents",
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
      />
    ),
  },
];

// Only admins can upload documents (v1: uploads are the only way documents
// enter an org's knowledge base, and only an admin can decide who gets to
// see them) — so the Upload link, like Admin, only renders for admins.
const uploadLink = {
  href: "/upload",
  label: "Upload",
  icon: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 7.5 12 3m0 0L7.5 7.5M12 3v13.5"
    />
  ),
};

const adminLink = {
  href: "/admin",
  label: "Admin",
  icon: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.286Z"
    />
  ),
};

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, logout } = useAuth();
  const links = user?.is_admin ? [baseLinks[0], uploadLink, baseLinks[1], adminLink] : baseLinks;

  const handleLogout = async () => {
    await logout();
    router.push("/login");
  };

  return (
    <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-900">
      <div className="flex items-center justify-between px-5 py-2.5">
        <div className="flex items-center gap-8">
          <Link href="/" className="flex items-center gap-2.5 text-sm font-semibold text-white">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-blue-500 to-indigo-600 text-xs font-bold text-white shadow-sm">
              R
            </span>
            <span className="tracking-tight">Enterprise RAG</span>
          </Link>

          {user && (
            <nav className="flex items-center gap-1 text-sm">
              {links.map((link) => {
                const isActive = pathname === link.href || pathname.startsWith(`${link.href}/`);
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-colors ${
                      isActive ? "bg-slate-800 text-white" : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
                    }`}
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.75} stroke="currentColor" className="h-4 w-4">
                      {link.icon}
                    </svg>
                    {link.label}
                  </Link>
                );
              })}
            </nav>
          )}
        </div>

        {!loading &&
          (user ? (
            <div className="flex min-w-0 items-center gap-3 border-l border-slate-800 pl-4">
              <div className="min-w-0 text-right leading-tight">
                <p className="max-w-[240px] truncate text-sm font-medium text-slate-100">{user.email}</p>
                <span className="mt-0.5 inline-block rounded-full bg-slate-800 px-2 py-0.5 text-[11px] font-medium text-slate-400">
                  {user.tenant_id}
                </span>
              </div>
              <button
                onClick={() => void handleLogout()}
                className="shrink-0 rounded-md border border-slate-700 px-3 py-1.5 text-sm font-medium text-slate-300 transition-colors hover:border-slate-600 hover:bg-slate-800 hover:text-white"
              >
                Log out
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Link
                href="/login"
                className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
              >
                Log in
              </Link>
              <Link
                href="/signup"
                className="rounded-md bg-gradient-to-br from-blue-600 to-indigo-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:shadow-md"
              >
                Sign up
              </Link>
            </div>
          ))}
      </div>
    </header>
  );
}

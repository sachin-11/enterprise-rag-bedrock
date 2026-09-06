"use client";

import { useEffect } from "react";

// Next.js App Router convention: this file automatically wraps everything
// under app/(protected)/admin in an error boundary. Without it, an
// uncaught render error anywhere on this page (e.g. a stale backend
// response missing a field the frontend expects — backend and frontend
// deploy independently here) white-screens the entire app instead of just
// this page.
export default function AdminError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("Admin dashboard render error:", error);
  }, [error]);

  return (
    <main className="flex min-h-0 flex-1 items-center justify-center px-6">
      <div className="max-w-md rounded-lg border border-red-200 bg-red-50 p-6 text-center">
        <p className="text-sm font-medium text-red-800">Something went wrong loading the admin dashboard</p>
        <p className="mt-2 text-xs text-red-600">
          This can happen if the backend was recently updated but hasn&apos;t fully deployed yet. Try reloading in a
          moment.
        </p>
        <button
          onClick={reset}
          className="mt-4 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700"
        >
          Try again
        </button>
      </div>
    </main>
  );
}

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { getInvitePreview, joinOrg } from "../lib/auth";

function JoinForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [orgSlug, setOrgSlug] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setPreviewError("This invite link is missing its token.");
      return;
    }
    getInvitePreview(token)
      .then((preview) => setOrgSlug(preview.org_slug))
      .catch((err: Error) => setPreviewError(err.message));
  }, [token]);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);
      setIsSubmitting(true);
      try {
        await joinOrg(token, email, password);
        router.push(`/confirm?email=${encodeURIComponent(email)}`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to join.");
      } finally {
        setIsSubmitting(false);
      }
    },
    [token, email, password, router],
  );

  if (previewError) {
    return <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{previewError}</p>;
  }

  if (orgSlug === null) {
    return <div className="h-24 animate-pulse rounded-lg bg-gray-100" />;
  }

  return (
    <>
      <p className="mb-6 text-sm text-gray-500">
        You&apos;re invited to join <span className="font-medium text-gray-900">{orgSlug}</span>.
      </p>

      <form onSubmit={(event) => void handleSubmit(event)} className="space-y-4">
        <div>
          <label htmlFor="email" className="mb-1 block text-sm font-medium text-gray-700">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="password" className="mb-1 block text-sm font-medium text-gray-700">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-gray-500">At least 8 characters, with uppercase, lowercase, and a number.</p>
        </div>

        {error && <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          {isSubmitting ? "Joining…" : `Join ${orgSlug}`}
        </button>
      </form>
    </>
  );
}

export default function JoinPage() {
  return (
    <main className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-16">
      <div className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Join your team</h1>

        <Suspense fallback={<div className="mt-6 h-24 animate-pulse rounded-lg bg-gray-100" />}>
          <JoinForm />
        </Suspense>

        <p className="mt-6 text-center text-sm text-gray-500">
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-blue-600 hover:text-blue-700">
            Log in
          </Link>
        </p>
      </div>
    </main>
  );
}

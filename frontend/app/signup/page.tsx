"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthProvider";

const ORG_SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$/;

export default function SignupPage() {
  const { user, loading, signup } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && user) router.replace("/chat");
  }, [loading, user, router]);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);

      if (!ORG_SLUG_PATTERN.test(orgSlug)) {
        setError("Organization ID must be lowercase letters, numbers, and hyphens only (e.g. acme-corp).");
        return;
      }

      setIsSubmitting(true);
      try {
        await signup(email, password, orgSlug);
        router.push(`/confirm?email=${encodeURIComponent(email)}`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Signup failed.");
      } finally {
        setIsSubmitting(false);
      }
    },
    [email, password, orgSlug, signup, router],
  );

  if (loading || user) return null;

  return (
    <main className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-16">
      <div className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Create an account</h1>
        <p className="mb-6 text-sm text-gray-500">
          This creates a brand-new organization. Joining an existing team? Use the invite link your admin sent you
          instead.
        </p>

        <form onSubmit={(event) => void handleSubmit(event)} className="space-y-4">
          <div>
            <label htmlFor="org_slug" className="mb-1 block text-sm font-medium text-gray-700">
              Organization ID
            </label>
            <input
              id="org_slug"
              type="text"
              required
              placeholder="acme-corp"
              value={orgSlug}
              onChange={(event) => setOrgSlug(event.target.value.toLowerCase())}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

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

          {error && (
            <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {isSubmitting ? "Creating account…" : "Sign up"}
          </button>
        </form>

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

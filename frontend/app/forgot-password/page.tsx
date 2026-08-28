"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { forgotPassword } from "../lib/auth";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);
      setIsSubmitting(true);
      try {
        await forgotPassword(email);
        setSubmitted(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Something went wrong.");
      } finally {
        setIsSubmitting(false);
      }
    },
    [email],
  );

  return (
    <main className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-16">
      <div className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Reset your password</h1>
        <p className="mb-6 text-sm text-gray-500">
          Enter your email and we&apos;ll send you a code to reset your password.
        </p>

        {submitted ? (
          <div className="space-y-4">
            <p className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">
              If an account with this email exists, a reset code has been sent.
            </p>
            <button
              onClick={() => router.push(`/reset-password?email=${encodeURIComponent(email)}`)}
              className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
            >
              I have a code
            </button>
          </div>
        ) : (
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

            {error && (
              <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
            )}

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              {isSubmitting ? "Sending…" : "Send reset code"}
            </button>
          </form>
        )}

        <p className="mt-6 text-center text-sm text-gray-500">
          <Link href="/login" className="font-medium text-blue-600 hover:text-blue-700">
            Back to login
          </Link>
        </p>
      </div>
    </main>
  );
}

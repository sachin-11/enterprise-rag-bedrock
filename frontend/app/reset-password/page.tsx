"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useState } from "react";
import { resetPassword } from "../lib/auth";

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [email, setEmail] = useState(searchParams.get("email") ?? "");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);
      setIsSubmitting(true);
      try {
        await resetPassword(email, code, newPassword);
        setSuccess(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Password reset failed.");
      } finally {
        setIsSubmitting(false);
      }
    },
    [email, code, newPassword],
  );

  if (success) {
    return (
      <div className="rounded-lg border border-green-200 bg-green-50 p-6 text-center">
        <p className="mb-3 font-medium text-green-800">Password reset</p>
        <button
          onClick={() => router.push("/login")}
          className="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-green-700"
        >
          Go to login
        </button>
      </div>
    );
  }

  return (
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
        <label htmlFor="code" className="mb-1 block text-sm font-medium text-gray-700">
          Reset code
        </label>
        <input
          id="code"
          type="text"
          required
          placeholder="123456"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>

      <div>
        <label htmlFor="new_password" className="mb-1 block text-sm font-medium text-gray-700">
          New password
        </label>
        <input
          id="new_password"
          type="password"
          required
          minLength={8}
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
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
        {isSubmitting ? "Resetting…" : "Reset password"}
      </button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <main className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-16">
      <div className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Set a new password</h1>
        <p className="mb-6 text-sm text-gray-500">Enter the code we emailed you along with your new password.</p>

        <Suspense fallback={null}>
          <ResetPasswordForm />
        </Suspense>

        <p className="mt-6 text-center text-sm text-gray-500">
          <Link href="/login" className="font-medium text-blue-600 hover:text-blue-700">
            Back to login
          </Link>
        </p>
      </div>
    </main>
  );
}

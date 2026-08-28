"use client";

import Link from "next/link";
import { useAuth } from "./context/AuthProvider";

const features = [
  {
    title: "Hybrid retrieval",
    description: "Combines dense vector search and BM25 keyword search, merged with reciprocal rank fusion.",
  },
  {
    title: "Tenant isolation",
    description: "Every query is scoped to a tenant at the retrieval layer, so data never crosses accounts.",
  },
  {
    title: "Cited answers",
    description: "Every answer links back to the exact source chunks it was generated from.",
  },
];

export default function Home() {
  const { user, loading } = useAuth();
  const isAuthed = !loading && !!user;

  return (
    <main className="mx-auto flex w-full min-h-0 max-w-5xl flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-20 text-center">
      <span className="mb-4 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
        Powered by AWS Bedrock
      </span>

      <h1 className="text-4xl font-bold tracking-tight text-gray-900 sm:text-5xl">
        Ask questions about your documents
      </h1>
      <p className="mt-4 max-w-xl text-base text-gray-500">
        Upload PDFs and DOCX files, then get cited, tenant-isolated answers powered by Claude on Bedrock.
      </p>

      <div className="mt-10 flex flex-col gap-3 sm:flex-row">
        {isAuthed ? (
          <>
            <Link
              href="/chat"
              className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
            >
              Start chatting
            </Link>
            <Link
              href="/upload"
              className="rounded-lg border border-gray-300 bg-white px-5 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:border-gray-400"
            >
              Upload a document
            </Link>
          </>
        ) : (
          <>
            <Link
              href="/signup"
              className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
            >
              Sign up
            </Link>
            <Link
              href="/login"
              className="rounded-lg border border-gray-300 bg-white px-5 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:border-gray-400"
            >
              Log in
            </Link>
          </>
        )}
      </div>

      <div className="mt-20 grid w-full gap-6 text-left sm:grid-cols-3">
        {features.map((feature) => (
          <div key={feature.title} className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">{feature.title}</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-gray-500">{feature.description}</p>
          </div>
        ))}
      </div>
    </main>
  );
}

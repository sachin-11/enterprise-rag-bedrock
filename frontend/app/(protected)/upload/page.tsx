"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../../context/AuthProvider";
import { isAllowedFile, syncKnowledgeBase, uploadFile } from "../../lib/documents";

type UploadState =
  | { status: "idle" }
  | { status: "uploading"; progress: number; file: File }
  | { status: "syncing"; documentId: string; s3Key: string }
  | { status: "success"; documentId: string; s3Key: string }
  | { status: "duplicate"; documentId: string; s3Key: string }
  | { status: "sync_failed"; documentId: string; s3Key: string; message: string }
  | { status: "error"; message: string; file: File };

export default function UploadPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [state, setState] = useState<UploadState>({ status: "idle" });
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (user && !user.is_admin) router.replace("/chat");
  }, [user, router]);

  const startUpload = useCallback((file: File) => {
    if (!isAllowedFile(file)) {
      setState({ status: "error", message: "Only .pdf and .docx files are supported.", file });
      return;
    }

    setState({ status: "uploading", progress: 0, file });

    uploadFile(file, (progress) => {
      setState((prev) => (prev.status === "uploading" ? { ...prev, progress } : prev));
    })
      .then((response) => {
        if (response.duplicate) {
          setState({ status: "duplicate", documentId: response.document_id, s3Key: response.s3_key });
          return null;
        }
        setState({ status: "syncing", documentId: response.document_id, s3Key: response.s3_key });
        return syncKnowledgeBase();
      })
      .then((result) => {
        if (result === null) return;
        setState((prev) =>
          prev.status === "syncing" ? { status: "success", documentId: prev.documentId, s3Key: prev.s3Key } : prev,
        );
      })
      .catch((error: Error) => {
        setState((prev) =>
          prev.status === "syncing"
            ? { status: "sync_failed", documentId: prev.documentId, s3Key: prev.s3Key, message: error.message }
            : { status: "error", message: error.message, file },
        );
      });
  }, []);

  const retrySync = useCallback(() => {
    setState((prev) => {
      if (prev.status !== "sync_failed") return prev;
      const { documentId, s3Key } = prev;
      syncKnowledgeBase()
        .then(() => setState({ status: "success", documentId, s3Key }))
        .catch((error: Error) => setState({ status: "sync_failed", documentId, s3Key, message: error.message }));
      return { status: "syncing", documentId, s3Key };
    });
  }, []);

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);
      const file = event.dataTransfer.files?.[0];
      if (file) startUpload(file);
    },
    [startUpload],
  );

  const handleInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (file) startUpload(file);
      event.target.value = "";
    },
    [startUpload],
  );

  const retry = useCallback(() => {
    if (state.status === "error") startUpload(state.file);
  }, [state, startUpload]);

  const reset = useCallback(() => setState({ status: "idle" }), []);

  const isUploading = state.status === "uploading";
  const showDropzone = state.status === "idle" || state.status === "uploading" || state.status === "error";

  if (!user?.is_admin) return null;

  return (
    <main className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 overflow-y-auto px-6 py-16">
      <div className="w-full max-w-lg rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Upload a document</h1>
        <p className="mb-6 text-sm text-gray-500">PDF or DOCX files up to 20MB.</p>

        {showDropzone && (
          <div
            onDragOver={(event) => {
              event.preventDefault();
              if (!isUploading) setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={isUploading ? undefined : handleDrop}
            onClick={() => !isUploading && inputRef.current?.click()}
            role="button"
            tabIndex={0}
            className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-12 text-center transition-colors ${
              isUploading ? "cursor-not-allowed border-gray-200 bg-gray-50 opacity-70" : "cursor-pointer"
            } ${isDragging && !isUploading ? "border-blue-500 bg-blue-50" : "border-gray-300 hover:border-gray-400"}`}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".pdf,.docx"
              className="hidden"
              onChange={handleInputChange}
              disabled={isUploading}
            />

            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
              className="h-10 w-10 text-gray-400"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 7.5 12 3m0 0L7.5 7.5M12 3v13.5"
              />
            </svg>

            <p className="text-sm text-gray-600">
              <span className="font-medium text-blue-600">Click to upload</span> or drag and drop
            </p>
          </div>
        )}

        {state.status === "uploading" && (
          <div className="mt-4">
            <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
              <span className="truncate">{state.file.name}</span>
              <span>{state.progress}%</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-gray-200">
              <div
                className="h-full rounded-full bg-blue-600 transition-all duration-150"
                style={{ width: `${state.progress}%` }}
              />
            </div>
          </div>
        )}

        {state.status === "syncing" && (
          <div className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 p-4">
            <div className="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-blue-300 border-t-blue-600" />
            <div>
              <p className="text-sm font-medium text-blue-800">Indexing into knowledge base…</p>
              <p className="text-xs text-blue-600">This can take a minute or two. Don&apos;t close this tab.</p>
            </div>
          </div>
        )}

        {state.status === "success" && (
          <div className="rounded-lg border border-green-200 bg-green-50 p-6">
            <p className="mb-3 font-medium text-green-800">Uploaded and indexed</p>
            <dl className="space-y-1 text-sm text-green-700">
              <div className="flex gap-2">
                <dt className="shrink-0 font-medium">Document ID:</dt>
                <dd className="break-all font-mono">{state.documentId}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="shrink-0 font-medium">S3 Key:</dt>
                <dd className="break-all font-mono">{state.s3Key}</dd>
              </div>
            </dl>
            <button
              onClick={reset}
              className="mt-4 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-green-700"
            >
              Upload another
            </button>
          </div>
        )}

        {state.status === "duplicate" && (
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-6">
            <p className="mb-1 font-medium text-gray-800">Already uploaded</p>
            <p className="text-sm text-gray-600">
              This exact file is already in the knowledge base — no changes detected, so it wasn&apos;t re-indexed.
            </p>
            <button
              onClick={reset}
              className="mt-4 rounded-md bg-gray-700 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-gray-800"
            >
              Upload another
            </button>
          </div>
        )}

        {state.status === "sync_failed" && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
            <p className="text-sm font-medium text-amber-800">Uploaded, but indexing failed</p>
            <p className="mt-1 text-sm text-amber-700">{state.message}</p>
            <p className="mt-1 text-xs text-amber-600">
              The file is safely in storage — it just isn&apos;t searchable in chat yet.
            </p>
            <button
              onClick={retrySync}
              className="mt-3 rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-700"
            >
              Retry indexing
            </button>
          </div>
        )}

        {state.status === "error" && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4">
            <p className="text-sm font-medium text-red-800">Upload failed</p>
            <p className="mt-1 text-sm text-red-600">{state.message}</p>
            <button
              onClick={retry}
              className="mt-3 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700"
            >
              Retry
            </button>
          </div>
        )}
      </div>
    </main>
  );
}

"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiFetch } from "../../lib/auth";
import { useAuth } from "../../context/AuthProvider";
import { ALLOWED_EXTENSIONS, isAllowedFile, syncKnowledgeBase, uploadFile } from "../../lib/documents";
import type { Message, Source } from "./types";

interface StreamEvent {
  type: "conversation" | "sources" | "token" | "error" | "done";
  conversation_id?: string;
  sources?: Source[];
  text?: string;
  detail?: string;
}

function createId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
}

/**
 * POSTs the query and yields each server-sent event as it arrives, so the
 * caller can render the answer token-by-token instead of waiting for the
 * whole response. Not built on EventSource (GET-only, no request body/custom
 * headers) — this reads the fetch Response body stream directly and parses
 * the same "data: {...}\n\n" wire format by hand.
 */
async function* streamChatQuery(
  query: string,
  conversationId: string | undefined,
): AsyncGenerator<StreamEvent> {
  const response = await apiFetch("/chat/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, conversation_id: conversationId ?? null }),
  });

  if (!response.ok || !response.body) {
    let message = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (body?.detail) message = body.detail;
    } catch {
      // fall back to the default message above
    }
    throw new Error(message);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, boundary).trim();
      buffer = buffer.slice(boundary + 2);
      if (!rawEvent.startsWith("data: ")) continue;
      try {
        yield JSON.parse(rawEvent.slice("data: ".length)) as StreamEvent;
      } catch {
        // Malformed/partial line — skip it rather than crash the stream.
      }
    }
  }
}

interface ChatViewProps {
  conversationId?: string;
  initialMessages?: Message[];
}

export function ChatView({ conversationId, initialMessages }: ChatViewProps) {
  const router = useRouter();
  const { user } = useAuth();
  const [messages, setMessages] = useState<Message[]>(initialMessages ?? []);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  // The in-progress assistant reply, updated on every token while streaming.
  // Kept separate from `messages` (rather than mutating an item in that
  // array on every token) and only "promoted" into a real Message once the
  // stream finishes — see handleSend.
  const [streamingMessage, setStreamingMessage] = useState<{ content: string; sources?: Source[] } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Tracks the conversation once the first message establishes it — a ref
  // (not state) because handleSend's closure needs the up-to-the-moment
  // value synchronously, before any re-render would land it in state.
  const activeConversationId = useRef(conversationId);

  const isUploadInFlight = useMemo(
    () => messages.some((m) => m.upload && (m.upload.status === "uploading" || m.upload.status === "syncing")),
    [messages],
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading, streamingMessage]);

  const updateUpload = useCallback((id: string, upload: Message["upload"]) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, upload } : m)));
  }, []);

  const handleFileSelected = useCallback(
    (file: File) => {
      if (!isAllowedFile(file)) {
        const id = createId();
        setMessages((prev) => [
          ...prev,
          {
            id,
            role: "assistant",
            content: "",
            upload: {
              fileName: file.name,
              status: "error",
              message: `Only ${ALLOWED_EXTENSIONS.join(", ")} files are supported.`,
            },
          },
        ]);
        return;
      }

      const id = createId();
      setMessages((prev) => [
        ...prev,
        { id, role: "assistant", content: "", upload: { fileName: file.name, status: "uploading", progress: 0 } },
      ]);

      uploadFile(file, (progress) => {
        updateUpload(id, { fileName: file.name, status: "uploading", progress });
      })
        .then((response) => {
          if (response.duplicate) {
            updateUpload(id, { fileName: file.name, status: "duplicate" });
            return null;
          }
          updateUpload(id, { fileName: file.name, status: "syncing" });
          return syncKnowledgeBase();
        })
        .then((result) => {
          if (result === null) return;
          updateUpload(id, { fileName: file.name, status: "success" });
        })
        .catch((error: Error) => {
          updateUpload(id, { fileName: file.name, status: "error", message: error.message });
        });
    },
    [updateUpload],
  );

  const handleFileInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (file) handleFileSelected(file);
      event.target.value = "";
    },
    [handleFileSelected],
  );

  const handleSend = useCallback(async () => {
    const query = input.trim();
    if (!query || isLoading) return;

    const userMessage: Message = { id: createId(), role: "user", content: query };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);
    setStreamingMessage(null);

    let text = "";
    let sources: Source[] | undefined;
    let newConversationId: string | null = null;
    let isError = false;

    try {
      for await (const event of streamChatQuery(query, activeConversationId.current)) {
        if (event.type === "conversation" && event.conversation_id) {
          // Captured now but the actual navigation is deferred to after the
          // stream finishes (see below) — redirecting mid-stream would
          // unmount this component (different route = different page.tsx)
          // and silently drop whatever hasn't streamed in yet.
          newConversationId = event.conversation_id;
        } else if (event.type === "sources" && event.sources) {
          // Captured but not shown yet — sources arrive before generation
          // even starts, so surfacing them immediately would show a
          // "Sources" block sitting above an empty bubble for a few
          // seconds. Keep the loading skeleton up until the first token
          // actually lands, then reveal sources and text together.
          sources = event.sources;
        } else if (event.type === "token" && event.text) {
          text += event.text;
          setStreamingMessage({ content: text, sources });
        } else if (event.type === "error" && event.detail) {
          isError = true;
          text = `Something went wrong: ${event.detail}`;
        }
      }
    } catch (error) {
      isError = true;
      text = `Something went wrong: ${error instanceof Error ? error.message : "unknown error"}`;
    }

    setMessages((prev) => [...prev, { id: createId(), role: "assistant", content: text, sources, isError }]);
    setStreamingMessage(null);
    setIsLoading(false);

    // First message of a new chat — the backend just created the
    // conversation. Move the URL to /chat/{id} so a refresh (or sharing the
    // link) lands back on this same conversation instead of a blank "new
    // chat" page. The target route independently re-fetches the
    // conversation's history on mount, so nothing here needs to hand off
    // the in-memory `messages` we already have.
    if (newConversationId && !activeConversationId.current) {
      activeConversationId.current = newConversationId;
      router.replace(`/chat/${newConversationId}`);
    }
  }, [input, isLoading, router]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void handleSend();
      }
    },
    [handleSend],
  );

  return (
    <main className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
        {messages.length === 0 && !isLoading ? (
          <div className="flex h-full items-center justify-center">
            <EmptyState isAdmin={user?.is_admin ?? false} />
          </div>
        ) : (
          <div className="mx-auto flex max-w-2xl flex-col gap-5">
            {messages.map((message) => (
              <ChatBubble key={message.id} message={message} />
            ))}

            {isLoading && streamingMessage === null && <LoadingBubble />}
            {isLoading && streamingMessage !== null && (
              <ChatBubble
                message={{
                  id: "streaming",
                  role: "assistant",
                  content: streamingMessage.content,
                  sources: streamingMessage.sources,
                }}
              />
            )}

            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="border-t border-gray-200 bg-white px-4 py-4">
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          {user?.is_admin && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept={ALLOWED_EXTENSIONS.join(",")}
                className="hidden"
                onChange={handleFileInputChange}
                disabled={isUploadInFlight}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploadInFlight}
                title="Attach a document (.pdf, .docx)"
                aria-label="Attach a document"
                className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-lg border border-gray-300 text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-700 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-300"
              >
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-5 w-5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13" />
                </svg>
              </button>
            </>
          )}
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            placeholder="Ask a question..."
            rows={1}
            className="flex-1 resize-none rounded-lg border border-gray-300 px-3.5 py-2.5 text-sm text-gray-900 shadow-sm placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
          />
          <button
            onClick={() => void handleSend()}
            disabled={isLoading || !input.trim()}
            aria-label="Send message"
            className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-sm transition-all hover:shadow-md disabled:cursor-not-allowed disabled:from-gray-300 disabled:to-gray-300 disabled:shadow-none"
          >
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="h-5 w-5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 10.5 12 3m0 0 7.5 7.5M12 3v18" />
            </svg>
          </button>
        </div>
      </div>
    </main>
  );
}

function EmptyState({ isAdmin }: { isAdmin: boolean }) {
  return (
    <div className="flex flex-col items-center text-center">
      <div className="mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 text-lg font-semibold text-white shadow-lg shadow-blue-500/20">
        AI
      </div>
      <h2 className="text-xl font-semibold text-gray-900">Ask about your documents</h2>
      <p className="mt-2 max-w-sm text-sm leading-relaxed text-gray-500">
        {isAdmin
          ? "Upload a PDF or DOCX, then ask a question — every answer is cited back to the exact source."
          : "Ask a question about the documents your admin has shared with you — every answer is cited back to the exact source."}
      </p>
    </div>
  );
}

function Avatar({ role, isError }: { role: Message["role"]; isError?: boolean }) {
  if (role === "user") {
    return (
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 text-xs font-semibold text-white shadow-sm">
        U
      </div>
    );
  }
  return (
    <div
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold shadow-sm ${
        isError ? "bg-red-100 text-red-600" : "bg-gradient-to-br from-slate-700 to-slate-900 text-white"
      }`}
    >
      {isError ? "!" : "AI"}
    </div>
  );
}

function UploadCard({ upload }: { upload: NonNullable<Message["upload"]> }) {
  return (
    <div className="flex items-start gap-3">
      <Avatar role="assistant" isError={upload.status === "error"} />
      <div className="max-w-[75%] rounded-2xl border border-gray-200 bg-white px-4 py-3 text-sm shadow-sm">
        <p className="truncate font-medium text-gray-700">{upload.fileName}</p>

        {upload.status === "uploading" && (
          <div className="mt-2">
            <div className="h-1.5 w-48 overflow-hidden rounded-full bg-gray-200">
              <div
                className="h-full rounded-full bg-blue-600 transition-all duration-150"
                style={{ width: `${upload.progress ?? 0}%` }}
              />
            </div>
            <p className="mt-1 text-xs text-gray-500">Uploading… {upload.progress ?? 0}%</p>
          </div>
        )}

        {upload.status === "syncing" && (
          <div className="mt-2 flex items-center gap-2">
            <div className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-blue-300 border-t-blue-600" />
            <p className="text-xs text-blue-600">Indexing into knowledge base… this can take a minute or two.</p>
          </div>
        )}

        {upload.status === "success" && (
          <p className="mt-1 text-xs text-green-600">Indexed — you can now ask questions about this document.</p>
        )}

        {upload.status === "duplicate" && (
          <p className="mt-1 text-xs text-gray-500">Already uploaded — this exact file is already indexed.</p>
        )}

        {upload.status === "error" && (
          <p className="mt-1 text-xs text-red-600">{upload.message ?? "Something went wrong."}</p>
        )}
      </div>
    </div>
  );
}

function ChatBubble({ message }: { message: Message }) {
  if (message.upload) {
    return <UploadCard upload={message.upload} />;
  }

  const isUser = message.role === "user";

  return (
    <div className={`flex items-start gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <Avatar role={message.role} isError={message.isError} />
      <div
        className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm ${
          isUser
            ? "bg-gradient-to-br from-blue-600 to-indigo-600 text-white"
            : message.isError
              ? "border border-red-200 bg-red-50 text-red-700"
              : "border border-gray-200 bg-white text-gray-900"
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <MarkdownContent content={message.content} />
        )}

        {message.sources && message.sources.length > 0 && (
          <details className="mt-2 text-xs">
            <summary className="cursor-pointer select-none font-medium text-gray-500 hover:text-gray-700">
              Sources ({message.sources.length})
            </summary>
            <ul className="mt-2 space-y-2">
              {message.sources.map((source, index) => (
                <li key={`${source.chunk_id}-${index}`} className="rounded-md border border-gray-200 bg-gray-50 p-2">
                  <p className="font-medium text-gray-700">{source.doc_name}</p>
                  <p className="mt-1 text-gray-500">{source.excerpt}</p>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="[&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-blue-600 underline hover:text-blue-700"
            >
              {children}
            </a>
          ),
          code: ({ children }) => (
            <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-xs">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="my-2 overflow-x-auto rounded-md bg-gray-900 p-2 text-xs text-gray-100">{children}</pre>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function LoadingBubble() {
  return (
    <div className="flex items-start gap-3">
      <Avatar role="assistant" />
      <div className="max-w-[75%] space-y-2 rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
        <div className="h-2.5 w-40 animate-pulse rounded bg-gray-200" />
        <div className="h-2.5 w-56 animate-pulse rounded bg-gray-200" />
        <div className="h-2.5 w-32 animate-pulse rounded bg-gray-200" />
      </div>
    </div>
  );
}

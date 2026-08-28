"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { getConversation } from "../../../lib/conversations";
import { ChatView } from "../ChatView";
import type { Message } from "../types";

function createId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
}

export default function ConversationPage() {
  const params = useParams<{ conversationId: string }>();
  const conversationId = params.conversationId;
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMessages(null);
    setError(null);

    getConversation(conversationId)
      .then((conversation) => {
        if (cancelled) return;
        setMessages(
          conversation.messages.map((message) => ({
            id: createId(),
            role: message.role,
            content: message.content,
            sources: message.sources.length > 0 ? message.sources : undefined,
            runId: message.run_id,
          })),
        );
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });

    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  if (error) {
    return (
      <main className="flex min-h-0 flex-1 items-center justify-center">
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      </main>
    );
  }

  if (messages === null) {
    return (
      <main className="flex min-h-0 flex-1 items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
      </main>
    );
  }

  // key forces a full remount when switching between conversations (e.g.
  // clicking a different sidebar row) — without it, ChatView's internal
  // activeConversationId ref would keep pointing at the previous id.
  return <ChatView key={conversationId} conversationId={conversationId} initialMessages={messages} />;
}

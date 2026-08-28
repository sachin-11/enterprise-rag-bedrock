"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { deleteConversation, listConversations, type ConversationSummary } from "../../lib/conversations";

export function ConversationSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // pathname changes fire load() often (every navigation between chats), so
  // overlapping requests can resolve out of order — e.g. a slow fetch from
  // the previous page landing after a newer one, clobbering fresh data with
  // stale data (this is exactly what caused a just-deleted conversation to
  // reappear). Only the response from the most recently *fired* request is
  // ever applied.
  const latestRequestId = useRef(0);

  const load = useCallback(() => {
    const requestId = ++latestRequestId.current;
    listConversations()
      .then((data) => {
        if (latestRequestId.current === requestId) setConversations(data);
      })
      .catch((err: Error) => {
        if (latestRequestId.current === requestId) setError(err.message);
      });
  }, []);

  // Re-fetch whenever the route changes — covers a brand-new conversation
  // (created by the first message of a chat) needing to appear in the list.
  useEffect(() => {
    load();
  }, [pathname, load]);

  const handleDelete = useCallback(
    (event: React.MouseEvent, conversationId: string) => {
      event.preventDefault();
      event.stopPropagation();

      const wasActive = pathname === `/chat/${conversationId}`;
      setConversations((prev) => (prev ? prev.filter((c) => c.conversation_id !== conversationId) : prev));

      deleteConversation(conversationId)
        .then(() => {
          if (wasActive) router.push("/chat");
        })
        .catch(() => {
          // Reload from the server rather than guessing the row's old position back in.
          load();
        });
    },
    [pathname, router, load],
  );

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-slate-800 bg-slate-900">
      <div className="p-3">
        <Link
          href="/chat"
          className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-white px-3 py-2 text-sm font-medium text-slate-900 shadow-sm transition-colors hover:bg-slate-100"
        >
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="h-4 w-4">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          New chat
        </Link>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {(conversations === null || (conversations && conversations.length > 0)) && (
          <p className="px-2.5 pb-1.5 pt-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Recent
          </p>
        )}

        {error && <p className="px-2.5 text-xs text-red-400">{error}</p>}

        {conversations === null && !error && (
          <div className="space-y-1.5 px-1">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-9 animate-pulse rounded-md bg-slate-800" />
            ))}
          </div>
        )}

        {conversations !== null && conversations.length === 0 && (
          <p className="px-2.5 py-1 text-xs text-slate-500">No conversations yet.</p>
        )}

        {conversations?.map((conversation) => {
          const href = `/chat/${conversation.conversation_id}`;
          const isActive = pathname === href;
          return (
            <Link
              key={conversation.conversation_id}
              href={href}
              className={`group relative flex items-center justify-between gap-1 rounded-md py-2 pl-2.5 pr-1.5 text-sm transition-colors ${
                isActive ? "bg-slate-800 text-white" : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
              }`}
            >
              {isActive && (
                <span className="absolute -left-2 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-blue-500" />
              )}
              <span className="truncate">{conversation.title}</span>
              <button
                onClick={(event) => handleDelete(event, conversation.conversation_id)}
                aria-label={`Delete conversation "${conversation.title}"`}
                className="shrink-0 rounded p-1 text-slate-500 opacity-0 transition-opacity hover:bg-slate-700 hover:text-slate-100 group-hover:opacity-100"
              >
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="h-3.5 w-3.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </button>
            </Link>
          );
        })}
      </div>
    </aside>
  );
}

import { apiFetch } from "./auth";
import type { Source } from "../(protected)/chat/types";

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  created_at: string;
  run_id?: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

async function parseErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    if (body?.detail) return body.detail;
  } catch {
    // fall back to the default message below
  }
  return fallback;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await apiFetch("/conversations");
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to load conversations (${response.status}).`));
  }
  const body = (await response.json()) as { conversations: ConversationSummary[] };
  return body.conversations;
}

export async function getConversation(conversationId: string): Promise<ConversationDetail> {
  const response = await apiFetch(`/conversations/${conversationId}`);
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to load conversation (${response.status}).`));
  }
  return (await response.json()) as ConversationDetail;
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await apiFetch(`/conversations/${conversationId}`, { method: "DELETE" });
  // A 404 means it's already gone — same "already achieved the desired end
  // state" reasoning as deleteDocument in lib/documents.ts.
  if (response.status === 404) return;
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to delete conversation (${response.status}).`));
  }
}

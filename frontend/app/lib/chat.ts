import { apiFetch } from "./auth";

async function parseErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    if (body?.detail) return body.detail;
  } catch {
    // fall back to the default message below
  }
  return fallback;
}

export async function submitFeedback(runId: string, isPositive: boolean): Promise<void> {
  const response = await apiFetch("/chat/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, is_positive: isPositive }),
  });
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to submit feedback (${response.status}).`));
  }
}

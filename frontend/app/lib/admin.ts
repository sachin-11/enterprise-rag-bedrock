import { apiFetch } from "./auth";

export interface OrgStats {
  query_count: number;
  error_count: number;
  error_rate: number;
  total_cost: number;
  avg_latency_s: number;
  p50_latency_s: number;
  p95_latency_s: number;
  feedback_count: number;
  feedback_positive_rate: number;
}

export interface ErrorRow {
  run_id: string;
  error: string;
  start_time: string;
  langsmith_url: string;
}

export interface OrgMember {
  sub: string;
  email: string;
  enabled: boolean;
  status: string;
  is_self: boolean;
  query_count: number;
  total_cost: number;
  avg_latency_s: number | null;
}

export interface RetryResult {
  succeeded: boolean;
  error: string | null;
  answer_preview: string | null;
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

export async function getOrgStats(days = 7): Promise<OrgStats> {
  const response = await apiFetch(`/admin/stats?days=${days}`);
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to load org stats (${response.status}).`));
  }
  return (await response.json()) as OrgStats;
}

export async function getRecentErrors(limit = 20): Promise<ErrorRow[]> {
  const response = await apiFetch(`/admin/errors?limit=${limit}`);
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to load recent errors (${response.status}).`));
  }
  const body = (await response.json()) as { errors: ErrorRow[] };
  return body.errors;
}

export async function getOrgMembers(days = 7): Promise<OrgMember[]> {
  const response = await apiFetch(`/admin/users?days=${days}`);
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to load organization members (${response.status}).`));
  }
  const body = (await response.json()) as { members: OrgMember[] };
  return body.members;
}

export async function suspendUser(sub: string): Promise<void> {
  const response = await apiFetch(`/admin/users/${sub}/suspend`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to suspend user (${response.status}).`));
  }
}

export async function unsuspendUser(sub: string): Promise<void> {
  const response = await apiFetch(`/admin/users/${sub}/unsuspend`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to unsuspend user (${response.status}).`));
  }
}

export async function retryFailedRun(runId: string): Promise<RetryResult> {
  const response = await apiFetch(`/admin/errors/${runId}/retry`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to retry run (${response.status}).`));
  }
  return (await response.json()) as RetryResult;
}

export interface InviteResult {
  invite_url: string;
  email_sent: boolean;
  email_error: string | null;
}

export async function generateInvite(email: string): Promise<InviteResult> {
  const response = await apiFetch("/admin/invites", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to send invite (${response.status}).`));
  }
  return (await response.json()) as InviteResult;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface AuthUser {
  email: string;
  tenant_id: string;
  is_admin: boolean;
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

// Coalesces concurrent 401s (e.g. several components fetching on mount) into
// a single /auth/refresh call instead of one per failed request.
let refreshInFlight: Promise<boolean> | null = null;

function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${API_BASE}/auth/refresh`, { method: "POST", credentials: "include" })
      .then((response) => response.ok)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

/**
 * fetch() wrapper for calls that expect an authenticated session: sends the
 * session cookie, and on a 401 (expired IdToken) makes one attempt to
 * silently refresh it via the RefreshToken cookie before retrying once.
 * Not used for signup/login themselves — a 401 there means wrong
 * credentials, not an expired session, so retrying via refresh is pointless.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
  const response = await fetch(url, { ...init, credentials: "include" });
  if (response.status !== 401) return response;

  const refreshed = await refreshSession();
  if (!refreshed) return response;

  return fetch(url, { ...init, credentials: "include" });
}

export async function signup(email: string, password: string, orgSlug: string): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/signup`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, org_slug: orgSlug }),
  });
  if (!response.ok) throw new Error(await parseErrorMessage(response, `Signup failed (${response.status}).`));
}

export async function confirmSignup(email: string, confirmationCode: string): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/confirm`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, confirmation_code: confirmationCode }),
  });
  if (!response.ok) throw new Error(await parseErrorMessage(response, `Confirmation failed (${response.status}).`));
}

export async function login(email: string, password: string): Promise<AuthUser> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) throw new Error(await parseErrorMessage(response, `Login failed (${response.status}).`));
  return (await response.json()) as AuthUser;
}

export async function forgotPassword(email: string): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/forgot-password`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to start password reset (${response.status}).`));
  }
}

export async function resetPassword(email: string, confirmationCode: string, newPassword: string): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/reset-password`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, confirmation_code: confirmationCode, new_password: newPassword }),
  });
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Password reset failed (${response.status}).`));
  }
}

export async function getInvitePreview(token: string): Promise<{ org_slug: string }> {
  const response = await fetch(`${API_BASE}/auth/invite-preview?token=${encodeURIComponent(token)}`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `This invite link is invalid (${response.status}).`));
  }
  return (await response.json()) as { org_slug: string };
}

export async function joinOrg(token: string, email: string, password: string): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/join`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, email, password }),
  });
  if (!response.ok) throw new Error(await parseErrorMessage(response, `Failed to join (${response.status}).`));
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" });
}

/** Restores the session from the httpOnly cookie on page load. Returns null if not logged in. */
export async function me(): Promise<AuthUser | null> {
  const response = await apiFetch("/auth/me");
  if (response.status === 401 || response.status === 403) return null;
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `Failed to load session (${response.status}).`));
  }
  return (await response.json()) as AuthUser;
}

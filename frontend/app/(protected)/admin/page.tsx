"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../../context/AuthProvider";
import {
  demoteFromAdmin,
  generateInvite,
  getAuditLog,
  getKnowledgeGaps,
  getOrgMembers,
  getOrgStats,
  getRecentErrors,
  promoteToAdmin,
  retryFailedRun,
  suspendUser,
  unsuspendUser,
  type AuditEventRow,
  type ErrorRow,
  type InviteResult,
  type KnowledgeGapRow,
  type OrgMember,
  type OrgStats,
  type RetryResult,
} from "../../lib/admin";

const DAYS = 7;
// Knowledge gaps benefit from a longer lookback than the rest of the
// dashboard — a rare-but-important missing-doc question might not repeat
// within just the last week.
const KNOWLEDGE_GAPS_DAYS = 30;
const AUDIT_LOG_DAYS = 30;

const AUDIT_ACTION_LABELS: Record<string, string> = {
  document_uploaded: "uploaded a document",
  document_deleted: "deleted a document",
  document_shared: "updated document sharing",
  user_suspended: "suspended a member",
  user_unsuspended: "unsuspended a member",
  user_promoted: "promoted a member to admin",
  user_demoted: "removed a member's admin access",
  invite_generated: "generated an invite",
};

type MemberRowState = "idle" | "working" | "error";
type RetryRowState = "idle" | "retrying" | "succeeded" | "failed";

function formatCost(value: number): string {
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}

function formatSeconds(value: number): string {
  return `${value.toFixed(2)}s`;
}

export default function AdminPage() {
  const { user } = useAuth();
  const router = useRouter();

  const [stats, setStats] = useState<OrgStats | null>(null);
  const [errors, setErrors] = useState<ErrorRow[] | null>(null);
  const [members, setMembers] = useState<OrgMember[] | null>(null);
  const [knowledgeGaps, setKnowledgeGaps] = useState<KnowledgeGapRow[] | null>(null);
  const [auditLog, setAuditLog] = useState<AuditEventRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [memberRowStates, setMemberRowStates] = useState<Record<string, MemberRowState>>({});
  const [memberRowErrors, setMemberRowErrors] = useState<Record<string, string>>({});
  const [retryRowStates, setRetryRowStates] = useState<Record<string, RetryRowState>>({});
  const [retryResults, setRetryResults] = useState<Record<string, RetryResult>>({});

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteResult, setInviteResult] = useState<InviteResult | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [isSendingInvite, setIsSendingInvite] = useState(false);
  const [inviteCopied, setInviteCopied] = useState(false);

  useEffect(() => {
    if (user && !user.is_admin) router.replace("/chat");
  }, [user, router]);

  const load = useCallback(() => {
    setLoadError(null);
    Promise.all([
      getOrgStats(DAYS),
      getRecentErrors(20),
      getOrgMembers(DAYS),
      getKnowledgeGaps(KNOWLEDGE_GAPS_DAYS),
      getAuditLog(AUDIT_LOG_DAYS),
    ])
      .then(([statsData, errorsData, membersData, gapsData, auditData]) => {
        setStats(statsData);
        setErrors(errorsData);
        setMembers(membersData);
        setKnowledgeGaps(gapsData);
        setAuditLog(auditData);
      })
      .catch((error: Error) => setLoadError(error.message));
  }, []);

  useEffect(() => {
    if (user?.is_admin) load();
  }, [user, load]);

  const handleSuspendToggle = (member: OrgMember) => {
    setMemberRowStates((prev) => ({ ...prev, [member.sub]: "working" }));
    setMemberRowErrors((prev) => {
      const next = { ...prev };
      delete next[member.sub];
      return next;
    });

    const action = member.enabled ? suspendUser : unsuspendUser;
    action(member.sub)
      .then(() => {
        setMembers((prev) =>
          prev ? prev.map((m) => (m.sub === member.sub ? { ...m, enabled: !m.enabled } : m)) : prev,
        );
        setMemberRowStates((prev) => ({ ...prev, [member.sub]: "idle" }));
      })
      .catch((error: Error) => {
        setMemberRowStates((prev) => ({ ...prev, [member.sub]: "error" }));
        setMemberRowErrors((prev) => ({ ...prev, [member.sub]: error.message }));
      });
  };

  const handleAdminToggle = (member: OrgMember) => {
    setMemberRowStates((prev) => ({ ...prev, [member.sub]: "working" }));
    setMemberRowErrors((prev) => {
      const next = { ...prev };
      delete next[member.sub];
      return next;
    });

    const action = member.is_admin ? demoteFromAdmin : promoteToAdmin;
    action(member.sub)
      .then(() => {
        setMembers((prev) =>
          prev ? prev.map((m) => (m.sub === member.sub ? { ...m, is_admin: !m.is_admin } : m)) : prev,
        );
        setMemberRowStates((prev) => ({ ...prev, [member.sub]: "idle" }));
      })
      .catch((error: Error) => {
        setMemberRowStates((prev) => ({ ...prev, [member.sub]: "error" }));
        setMemberRowErrors((prev) => ({ ...prev, [member.sub]: error.message }));
      });
  };

  const handleRetry = (runId: string) => {
    setRetryRowStates((prev) => ({ ...prev, [runId]: "retrying" }));
    retryFailedRun(runId)
      .then((result) => {
        setRetryResults((prev) => ({ ...prev, [runId]: result }));
        setRetryRowStates((prev) => ({ ...prev, [runId]: result.succeeded ? "succeeded" : "failed" }));
      })
      .catch((error: Error) => {
        setRetryResults((prev) => ({ ...prev, [runId]: { succeeded: false, error: error.message, answer_preview: null } }));
        setRetryRowStates((prev) => ({ ...prev, [runId]: "failed" }));
      });
  };

  const handleSendInvite = (event: React.FormEvent) => {
    event.preventDefault();
    setInviteError(null);
    setIsSendingInvite(true);
    generateInvite(inviteEmail)
      .then((result) => {
        setInviteResult(result);
        setIsSendingInvite(false);
      })
      .catch((error: Error) => {
        setInviteError(error.message);
        setIsSendingInvite(false);
      });
  };

  const handleCopyInvite = () => {
    if (!inviteResult) return;
    navigator.clipboard
      .writeText(inviteResult.invite_url)
      .then(() => {
        setInviteCopied(true);
        setTimeout(() => setInviteCopied(false), 2000);
      })
      .catch(() => {
        // Clipboard write can fail (e.g. no permission) — the link is still
        // visible and selectable in the input, so this isn't fatal.
      });
  };

  const resetInvite = () => {
    setInviteResult(null);
    setInviteError(null);
  };

  if (!user?.is_admin) return null;

  return (
    <main className="min-h-0 flex-1 overflow-y-auto px-6 py-10">
      <div className="mx-auto w-full max-w-5xl">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Admin dashboard</h1>
        <p className="mb-6 text-sm text-gray-500">
          Cost, latency, and error diagnostics for <span className="font-medium text-gray-700">{user.tenant_id}</span> — last {DAYS} days.
        </p>

        {loadError && (
          <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4">
            <p className="text-sm font-medium text-red-800">Couldn&apos;t load dashboard data</p>
            <p className="mt-1 text-sm text-red-600">{loadError}</p>
            <button
              onClick={load}
              className="mt-3 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700"
            >
              Retry
            </button>
          </div>
        )}

        {/* Invite teammate */}
        <section className="mb-8 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <h2 className="mb-1 text-sm font-semibold text-gray-900">Invite a teammate</h2>
          <p className="mb-3 text-xs text-gray-500">
            Enter their email — we&apos;ll send them a link to join{" "}
            <span className="font-medium text-gray-700">{user.tenant_id}</span> as a member (valid 7 days). They
            won&apos;t see any document until you share it with them.
          </p>

          {!inviteResult && (
            <form onSubmit={handleSendInvite} className="flex items-center gap-2">
              <input
                type="email"
                required
                placeholder="teammate@example.com"
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
                className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-xs text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
              <button
                type="submit"
                disabled={isSendingInvite}
                className="shrink-0 rounded-md bg-gradient-to-br from-blue-600 to-indigo-600 px-4 py-2 text-xs font-medium text-white shadow-sm transition-all hover:shadow-md disabled:opacity-60"
              >
                {isSendingInvite ? "Sending…" : "Send invite"}
              </button>
            </form>
          )}

          {inviteResult?.email_sent && (
            <div className="rounded-md border border-green-200 bg-green-50 p-3">
              <p className="text-xs font-medium text-green-800">Invite sent to {inviteEmail}</p>
              <div className="mt-2 flex items-center gap-2">
                <input
                  readOnly
                  value={inviteResult.invite_url}
                  onFocus={(event) => event.target.select()}
                  className="flex-1 rounded-md border border-green-300 bg-white px-3 py-2 text-xs text-gray-700"
                />
                <button
                  onClick={handleCopyInvite}
                  className="shrink-0 rounded-md border border-green-300 bg-white px-3 py-2 text-xs font-medium text-green-700 transition-colors hover:bg-green-50"
                >
                  {inviteCopied ? "Copied!" : "Copy"}
                </button>
              </div>
              <button onClick={resetInvite} className="mt-2 text-xs font-medium text-blue-600 hover:underline">
                Invite someone else
              </button>
            </div>
          )}

          {inviteResult && !inviteResult.email_sent && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
              <p className="text-xs font-medium text-amber-800">Couldn&apos;t email this automatically</p>
              <p className="mt-1 text-xs text-amber-700">{inviteResult.email_error}</p>
              <p className="mt-2 text-xs text-amber-600">Copy this link and send it yourself instead:</p>
              <div className="mt-2 flex items-center gap-2">
                <input
                  readOnly
                  value={inviteResult.invite_url}
                  onFocus={(event) => event.target.select()}
                  className="flex-1 rounded-md border border-amber-300 bg-white px-3 py-2 text-xs text-gray-700"
                />
                <button
                  onClick={handleCopyInvite}
                  className="shrink-0 rounded-md border border-amber-300 bg-white px-3 py-2 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-50"
                >
                  {inviteCopied ? "Copied!" : "Copy"}
                </button>
              </div>
              <button onClick={resetInvite} className="mt-2 text-xs font-medium text-blue-600 hover:underline">
                Try again
              </button>
            </div>
          )}

          {inviteError && <p className="mt-2 text-xs text-red-600">{inviteError}</p>}
        </section>

        {/* Stat cards */}
        <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {[
            { label: "Queries", value: stats ? stats.query_count.toLocaleString() : null },
            { label: "Total cost", value: stats ? formatCost(stats.total_cost) : null },
            { label: "Avg / p95 latency", value: stats ? `${formatSeconds(stats.avg_latency_s)} / ${formatSeconds(stats.p95_latency_s)}` : null },
            { label: "Error rate", value: stats ? `${(stats.error_rate * 100).toFixed(1)}%` : null },
            {
              label: "Helpful",
              value: stats
                ? stats.feedback_count === 0
                  ? "No feedback yet"
                  : `${(stats.feedback_positive_rate * 100).toFixed(0)}% (${stats.feedback_count})`
                : null,
            },
            {
              label: "Cache hits",
              value: stats ? `${stats.cache_hit_count.toLocaleString()} (${(stats.cache_hit_rate * 100).toFixed(0)}%)` : null,
            },
            {
              label: "Cost saved by cache",
              value: stats ? formatCost(stats.estimated_cost_saved) : null,
            },
          ].map((card) => (
            <div key={card.label} className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-medium uppercase tracking-wide text-gray-500">{card.label}</p>
              {card.value === null ? (
                <div className="mt-2 h-6 w-16 animate-pulse rounded bg-gray-100" />
              ) : (
                <p className="mt-1 text-xl font-semibold text-gray-900">{card.value}</p>
              )}
            </div>
          ))}
        </div>

        {/* Recent errors */}
        <section className="mb-8">
          <h2 className="mb-3 text-sm font-semibold text-gray-900">Recent errors</h2>

          {errors === null && !loadError && (
            <div className="space-y-2">
              {[0, 1].map((i) => (
                <div key={i} className="h-14 animate-pulse rounded-lg border border-gray-200 bg-gray-50" />
              ))}
            </div>
          )}

          {errors !== null && errors.length === 0 && (
            <div className="rounded-lg border border-dashed border-gray-300 p-6 text-center">
              <p className="text-sm text-gray-500">No errors in the last {DAYS} days.</p>
            </div>
          )}

          {errors !== null && errors.length > 0 && (
            <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white shadow-sm">
              {errors.map((row) => {
                const rowState = retryRowStates[row.run_id] ?? "idle";
                const result = retryResults[row.run_id];
                return (
                  <li key={row.run_id} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-gray-900">{row.error}</p>
                        <p className="mt-0.5 text-xs text-gray-500">
                          {new Date(row.start_time).toLocaleString()} ·{" "}
                          <a
                            href={row.langsmith_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-blue-600 hover:underline"
                          >
                            View trace
                          </a>
                        </p>
                      </div>

                      <div className="flex shrink-0 items-center gap-2">
                        {rowState === "idle" && (
                          <button
                            onClick={() => handleRetry(row.run_id)}
                            className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
                          >
                            Retry
                          </button>
                        )}
                        {rowState === "retrying" && (
                          <span className="flex items-center gap-1.5 text-xs text-gray-500">
                            <span className="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
                            Retrying…
                          </span>
                        )}
                        {rowState === "succeeded" && (
                          <span className="rounded-full bg-green-50 px-2.5 py-1 text-xs font-medium text-green-700">
                            Fixed
                          </span>
                        )}
                        {rowState === "failed" && (
                          <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs font-medium text-red-700">
                            Still failing
                          </span>
                        )}
                      </div>
                    </div>
                    {(rowState === "succeeded" || rowState === "failed") && result && (
                      <p className="mt-2 truncate text-xs text-gray-500">
                        {result.succeeded ? result.answer_preview : result.error}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Knowledge gaps */}
        <section className="mb-8">
          <h2 className="mb-1 text-sm font-semibold text-gray-900">Knowledge gaps</h2>
          <p className="mb-3 text-xs text-gray-500">
            Questions from the last {KNOWLEDGE_GAPS_DAYS} days that got zero relevant documents back — the
            strongest signal for what&apos;s missing from your knowledge base.
          </p>

          {knowledgeGaps === null && !loadError && (
            <div className="space-y-2">
              {[0, 1].map((i) => (
                <div key={i} className="h-14 animate-pulse rounded-lg border border-gray-200 bg-gray-50" />
              ))}
            </div>
          )}

          {knowledgeGaps !== null && knowledgeGaps.length === 0 && (
            <div className="rounded-lg border border-dashed border-gray-300 p-6 text-center">
              <p className="text-sm text-gray-500">No knowledge gaps in the last {KNOWLEDGE_GAPS_DAYS} days.</p>
            </div>
          )}

          {knowledgeGaps !== null && knowledgeGaps.length > 0 && (
            <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white shadow-sm">
              {knowledgeGaps.map((gap, index) => (
                <li key={`${gap.query}-${index}`} className="flex items-center justify-between gap-4 px-4 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-900">{gap.query}</p>
                    <p className="mt-0.5 text-xs text-gray-500">Last asked {new Date(gap.last_asked).toLocaleString()}</p>
                  </div>
                  <span className="shrink-0 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
                    {gap.occurrence_count}×
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Members */}
        <section>
          <h2 className="mb-3 text-sm font-semibold text-gray-900">Team members</h2>

          {members === null && !loadError && (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-14 animate-pulse rounded-lg border border-gray-200 bg-gray-50" />
              ))}
            </div>
          )}

          {members !== null && (
            <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white shadow-sm">
              {members.map((member) => {
                const rowState = memberRowStates[member.sub] ?? "idle";
                return (
                  <li key={member.sub} className="flex items-center justify-between gap-4 px-4 py-3">
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 truncate text-sm font-medium text-gray-900">
                        {member.email}
                        {member.is_self && (
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-500">
                            You
                          </span>
                        )}
                        {member.is_admin && (
                          <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600">
                            Admin
                          </span>
                        )}
                        {!member.enabled && (
                          <span className="rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-600">
                            Suspended
                          </span>
                        )}
                      </p>
                      <p className="mt-0.5 text-xs text-gray-500">
                        {member.query_count.toLocaleString()} queries · {formatCost(member.total_cost)}
                        {member.avg_latency_s !== null && ` · ${formatSeconds(member.avg_latency_s)} avg`}
                      </p>
                      {rowState === "error" && (
                        <p className="mt-1 text-xs text-red-600">{memberRowErrors[member.sub]}</p>
                      )}
                    </div>

                    <div className="flex shrink-0 items-center gap-2">
                      {member.is_self ? (
                        <span className="text-xs text-gray-400">—</span>
                      ) : rowState === "working" ? (
                        <span className="flex items-center gap-1.5 text-xs text-gray-500">
                          <span className="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
                          Working…
                        </span>
                      ) : (
                        <>
                          <button
                            onClick={() => handleAdminToggle(member)}
                            className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
                          >
                            {member.is_admin ? "Remove admin" : "Make admin"}
                          </button>
                          {member.enabled ? (
                            <button
                              onClick={() => handleSuspendToggle(member)}
                              className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-700"
                            >
                              Suspend
                            </button>
                          ) : (
                            <button
                              onClick={() => handleSuspendToggle(member)}
                              className="rounded-md bg-gradient-to-br from-blue-600 to-indigo-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-all hover:shadow-md"
                            >
                              Unsuspend
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Audit log */}
        <section className="mt-8">
          <h2 className="mb-1 text-sm font-semibold text-gray-900">Audit log</h2>
          <p className="mb-3 text-xs text-gray-500">
            Admin actions from the last {AUDIT_LOG_DAYS} days — who did what, and when.
          </p>

          {auditLog === null && !loadError && (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-12 animate-pulse rounded-lg border border-gray-200 bg-gray-50" />
              ))}
            </div>
          )}

          {auditLog !== null && auditLog.length === 0 && (
            <div className="rounded-lg border border-dashed border-gray-300 p-6 text-center">
              <p className="text-sm text-gray-500">No admin activity in the last {AUDIT_LOG_DAYS} days.</p>
            </div>
          )}

          {auditLog !== null && auditLog.length > 0 && (
            <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white shadow-sm">
              {auditLog.map((event, index) => (
                <li key={`${event.created_at}-${index}`} className="px-4 py-2.5">
                  <p className="text-sm text-gray-900">
                    <span className="font-medium">{event.actor_email}</span>{" "}
                    {AUDIT_ACTION_LABELS[event.action] ?? event.action}
                    {event.target && <span className="text-gray-600"> — {event.target}</span>}
                  </p>
                  <p className="mt-0.5 text-xs text-gray-500">
                    {new Date(event.created_at).toLocaleString()}
                    {event.details && ` · ${event.details}`}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}

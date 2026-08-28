"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../../context/AuthProvider";
import { getOrgMembers, type OrgMember } from "../../lib/admin";
import {
  deleteDocument,
  getDocumentShares,
  listDocuments,
  syncKnowledgeBase,
  updateDocumentShares,
  type DocumentSummary,
} from "../../lib/documents";

type RowState = "idle" | "confirming" | "deleting" | "error";
type ShareRowState = "idle" | "loading" | "saving" | "error";

// Keeps the loading skeleton visible for at least this long so it doesn't
// flash by unnoticed when the list resolves quickly.
const MIN_LOADING_MS = 400;

export default function DocumentsPage() {
  const { user } = useAuth();
  const isAdmin = user?.is_admin ?? false;

  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rowStates, setRowStates] = useState<Record<string, RowState>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  // Sharing panel: only one document's panel is open at a time. Org members
  // are fetched lazily on first open and cached across rows.
  const [openShareId, setOpenShareId] = useState<string | null>(null);
  const [orgMembers, setOrgMembers] = useState<OrgMember[] | null>(null);
  const [shareSelections, setShareSelections] = useState<Record<string, string[]>>({});
  const [shareRowStates, setShareRowStates] = useState<Record<string, ShareRowState>>({});
  const [shareRowErrors, setShareRowErrors] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    setLoadError(null);
    setDocuments(null);
    const startedAt = Date.now();

    listDocuments()
      .then(async (docs) => {
        const remaining = MIN_LOADING_MS - (Date.now() - startedAt);
        if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining));
        setDocuments(docs);
      })
      .catch((error: Error) => setLoadError(error.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const setRowState = (id: string, state: RowState) => setRowStates((prev) => ({ ...prev, [id]: state }));

  const requestDelete = (id: string) => setRowState(id, "confirming");
  const cancelDelete = (id: string) => setRowState(id, "idle");

  // Guards against a double-click (or a fast repeat click before React
  // re-renders) firing two DELETE requests for the same row — a ref is used
  // instead of rowStates because it's read/written synchronously, unlike state.
  const deletesInFlight = useRef<Set<string>>(new Set());

  const confirmDelete = useCallback((documentId: string) => {
    if (deletesInFlight.current.has(documentId)) return;
    deletesInFlight.current.add(documentId);

    setRowState(documentId, "deleting");
    setRowErrors((prev) => {
      const next = { ...prev };
      delete next[documentId];
      return next;
    });

    deleteDocument(documentId)
      .then(() => {
        // The file is already gone from S3 at this point — that's the part
        // the user is waiting on, so drop the row now instead of blocking
        // on a full KB resync (which can take a couple of minutes).
        setDocuments((prev) => (prev ? prev.filter((doc) => doc.document_id !== documentId) : prev));
        deletesInFlight.current.delete(documentId);

        // Re-sync in the background so retrieval catches up; nothing left in
        // the UI needs to reflect this, so just log if it fails.
        syncKnowledgeBase().catch((error: Error) => {
          console.error(`Background KB sync failed after deleting document ${documentId}:`, error);
        });
      })
      .catch((error: Error) => {
        setRowState(documentId, "error");
        setRowErrors((prev) => ({ ...prev, [documentId]: error.message }));
        deletesInFlight.current.delete(documentId);
      });
  }, []);

  const toggleShare = useCallback(
    (documentId: string) => {
      if (openShareId === documentId) {
        setOpenShareId(null);
        return;
      }

      setOpenShareId(documentId);
      setShareRowStates((prev) => ({ ...prev, [documentId]: "loading" }));
      setShareRowErrors((prev) => {
        const next = { ...prev };
        delete next[documentId];
        return next;
      });

      const membersPromise = orgMembers ? Promise.resolve(orgMembers) : getOrgMembers();
      Promise.all([membersPromise, getDocumentShares(documentId)])
        .then(([members, sharing]) => {
          setOrgMembers(members);
          setShareSelections((prev) => ({ ...prev, [documentId]: sharing.shared_with }));
          setShareRowStates((prev) => ({ ...prev, [documentId]: "idle" }));
        })
        .catch((error: Error) => {
          setShareRowStates((prev) => ({ ...prev, [documentId]: "error" }));
          setShareRowErrors((prev) => ({ ...prev, [documentId]: error.message }));
        });
    },
    [openShareId, orgMembers],
  );

  const toggleShareMember = useCallback((documentId: string, sub: string) => {
    setShareSelections((prev) => {
      const current = prev[documentId] ?? [];
      const next = current.includes(sub) ? current.filter((id) => id !== sub) : [...current, sub];
      return { ...prev, [documentId]: next };
    });
  }, []);

  const saveShares = useCallback((documentId: string) => {
    const selection = shareSelections[documentId] ?? [];
    setShareRowStates((prev) => ({ ...prev, [documentId]: "saving" }));

    updateDocumentShares(documentId, selection)
      .then(() => {
        setShareRowStates((prev) => ({ ...prev, [documentId]: "idle" }));
        setOpenShareId(null);
        // Sharing only takes effect in retrieval after the KB re-syncs —
        // same fire-and-forget pattern as the delete flow below.
        syncKnowledgeBase().catch((error: Error) => {
          console.error(`Background KB sync failed after updating sharing for ${documentId}:`, error);
        });
      })
      .catch((error: Error) => {
        setShareRowStates((prev) => ({ ...prev, [documentId]: "error" }));
        setShareRowErrors((prev) => ({ ...prev, [documentId]: error.message }));
      });
  }, [shareSelections]);

  return (
    <main className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto px-6 py-16">
      <div className="w-full max-w-2xl">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Your documents</h1>
        <p className="mb-6 text-sm text-gray-500">Documents indexed into your knowledge base.</p>

        {loadError && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-4">
            <p className="text-sm font-medium text-red-800">Couldn&apos;t load documents</p>
            <p className="mt-1 text-sm text-red-600">{loadError}</p>
            <button
              onClick={load}
              className="mt-3 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700"
            >
              Retry
            </button>
          </div>
        )}

        {documents === null && !loadError && (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-14 animate-pulse rounded-lg border border-gray-200 bg-gray-50" />
            ))}
          </div>
        )}

        {documents !== null && documents.length === 0 && (
          <div className="rounded-lg border border-dashed border-gray-300 p-10 text-center">
            <p className="text-sm text-gray-500">No documents uploaded yet.</p>
          </div>
        )}

        {documents !== null && documents.length > 0 && (
          <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white shadow-sm">
            {documents.map((doc) => {
              const rowState = rowStates[doc.document_id] ?? "idle";
              const shareState = shareRowStates[doc.document_id] ?? "idle";
              const isShareOpen = openShareId === doc.document_id;
              return (
                <li key={doc.document_id} className="px-4 py-3">
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-gray-900">{doc.filename}</p>
                      <p className="mt-0.5 text-xs text-gray-500">
                        {new Date(doc.upload_timestamp).toLocaleString()} · {doc.status}
                      </p>
                      {rowState === "error" && (
                        <p className="mt-1 text-xs text-red-600">{rowErrors[doc.document_id]}</p>
                      )}
                    </div>

                    {isAdmin && (
                      <div className="flex shrink-0 items-center gap-2">
                        {rowState === "idle" && (
                          <>
                            <button
                              onClick={() => toggleShare(doc.document_id)}
                              className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                                isShareOpen
                                  ? "border-blue-300 bg-blue-50 text-blue-700"
                                  : "border-gray-300 text-gray-600 hover:bg-gray-50"
                              }`}
                            >
                              Share
                            </button>
                            <button
                              onClick={() => requestDelete(doc.document_id)}
                              className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-700"
                            >
                              Delete
                            </button>
                          </>
                        )}

                        {rowState === "confirming" && (
                          <>
                            <span className="text-xs text-gray-500">Delete?</span>
                            <button
                              onClick={() => confirmDelete(doc.document_id)}
                              className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-700"
                            >
                              Confirm
                            </button>
                            <button
                              onClick={() => cancelDelete(doc.document_id)}
                              className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
                            >
                              Cancel
                            </button>
                          </>
                        )}

                        {rowState === "deleting" && (
                          <span className="flex items-center gap-1.5 text-xs text-gray-500">
                            <span className="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
                            Deleting…
                          </span>
                        )}

                        {rowState === "error" && (
                          <button
                            onClick={() => confirmDelete(doc.document_id)}
                            className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-700"
                          >
                            Retry delete
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  {isAdmin && isShareOpen && (
                    <div className="mt-3 rounded-md border border-gray-200 bg-gray-50 p-3">
                      {shareState === "loading" && <div className="h-16 animate-pulse rounded bg-gray-100" />}

                      {shareState === "error" && (
                        <p className="text-xs text-red-600">{shareRowErrors[doc.document_id]}</p>
                      )}

                      {shareState !== "loading" && orgMembers && (
                        <>
                          <p className="mb-2 text-xs font-medium text-gray-700">Share with:</p>
                          {orgMembers.filter((member) => !member.is_self).length === 0 ? (
                            <p className="text-xs text-gray-500">No other members in your org yet.</p>
                          ) : (
                            <div className="space-y-1.5">
                              {orgMembers
                                .filter((member) => !member.is_self)
                                .map((member) => (
                                  <label key={member.sub} className="flex items-center gap-2 text-xs text-gray-700">
                                    <input
                                      type="checkbox"
                                      checked={(shareSelections[doc.document_id] ?? []).includes(member.sub)}
                                      onChange={() => toggleShareMember(doc.document_id, member.sub)}
                                      className="h-3.5 w-3.5 rounded border-gray-300"
                                    />
                                    {member.email}
                                  </label>
                                ))}
                            </div>
                          )}
                          <div className="mt-3 flex items-center gap-2">
                            <button
                              onClick={() => saveShares(doc.document_id)}
                              disabled={shareState === "saving"}
                              className="rounded-md bg-gradient-to-br from-blue-600 to-indigo-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-all hover:shadow-md disabled:opacity-60"
                            >
                              {shareState === "saving" ? "Saving…" : "Save"}
                            </button>
                            <button
                              onClick={() => setOpenShareId(null)}
                              className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-white"
                            >
                              Cancel
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </main>
  );
}

import { apiFetch } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const UPLOAD_URL = `${API_BASE}/documents/upload`;
export const SYNC_URL = `${API_BASE}/documents/sync-kb`;
export const LIST_URL = `${API_BASE}/documents`;
export const deleteDocumentUrl = (documentId: string) => `${API_BASE}/documents/${documentId}`;

export const ALLOWED_EXTENSIONS = [".pdf", ".docx"];

export interface UploadResponse {
  document_id: string;
  s3_key: string;
  status: string;
  duplicate: boolean;
}

export function isAllowedFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

export function uploadFile(file: File, onProgress: (percent: number) => void): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", UPLOAD_URL);
    // XHR is used here (not apiFetch) for upload-progress events, so the
    // session cookie is sent via withCredentials rather than fetch's
    // credentials:"include". Unlike apiFetch, this does not retry on a 401
    // from an expired session — an in-progress upload failing that way is
    // rare enough (60-minute token lifetime) that surfacing the error and
    // letting the user retry is an acceptable simplification.
    xhr.withCredentials = true;

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadResponse);
        } catch {
          reject(new Error("Received an invalid response from the server."));
        }
      } else {
        let message = `Upload failed (${xhr.status}).`;
        try {
          const body = JSON.parse(xhr.responseText) as { detail?: string };
          if (body?.detail) message = body.detail;
        } catch {
          // fall back to the default message above
        }
        reject(new Error(message));
      }
    };

    xhr.onerror = () => reject(new Error("Network error — is the backend running?"));

    xhr.send(formData);
  });
}

export async function syncKnowledgeBase(): Promise<void> {
  const response = await apiFetch("/documents/sync-kb", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });

  if (!response.ok) {
    let message = `Sync failed (${response.status}).`;
    try {
      const body = await response.json();
      if (body?.detail) message = body.detail;
    } catch {
      // fall back to the default message above
    }
    throw new Error(message);
  }
}

export interface DocumentSummary {
  document_id: string;
  filename: string;
  upload_timestamp: string;
  status: string;
}

export async function listDocuments(): Promise<DocumentSummary[]> {
  const response = await apiFetch("/documents");

  if (!response.ok) {
    let message = `Failed to load documents (${response.status}).`;
    try {
      const body = await response.json();
      if (body?.detail) message = body.detail;
    } catch {
      // fall back to the default message above
    }
    throw new Error(message);
  }

  const body = (await response.json()) as { documents: DocumentSummary[] };
  return body.documents;
}

export interface DocumentSharing {
  document_id: string;
  uploaded_by: string;
  shared_with: string[];
}

export async function getDocumentShares(documentId: string): Promise<DocumentSharing> {
  const response = await apiFetch(`/documents/${documentId}/shares`);

  if (!response.ok) {
    let message = `Failed to load document sharing (${response.status}).`;
    try {
      const body = await response.json();
      if (body?.detail) message = body.detail;
    } catch {
      // fall back to the default message above
    }
    throw new Error(message);
  }

  return (await response.json()) as DocumentSharing;
}

export async function updateDocumentShares(documentId: string, userIds: string[]): Promise<DocumentSharing> {
  const response = await apiFetch(`/documents/${documentId}/shares`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_ids: userIds }),
  });

  if (!response.ok) {
    let message = `Failed to update document sharing (${response.status}).`;
    try {
      const body = await response.json();
      if (body?.detail) message = body.detail;
    } catch {
      // fall back to the default message above
    }
    throw new Error(message);
  }

  return (await response.json()) as DocumentSharing;
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await apiFetch(`/documents/${documentId}`, { method: "DELETE" });

  // A 404 means the document is already gone (e.g. a duplicate request from
  // a double-click raced an earlier successful delete) — that's the caller's
  // desired end state, not a failure, so treat it the same as success.
  if (response.status === 404) return;

  if (!response.ok) {
    let message = `Failed to delete document (${response.status}).`;
    try {
      const body = await response.json();
      if (body?.detail) message = body.detail;
    } catch {
      // fall back to the default message above
    }
    throw new Error(message);
  }
}

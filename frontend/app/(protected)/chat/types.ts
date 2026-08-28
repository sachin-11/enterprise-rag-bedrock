export interface Source {
  chunk_id: string;
  doc_name: string;
  excerpt: string;
}

export type ChatRole = "user" | "assistant";

export type UploadStatus = "uploading" | "syncing" | "success" | "duplicate" | "error";

export interface UploadState {
  fileName: string;
  status: UploadStatus;
  progress?: number;
  message?: string;
}

export interface Message {
  id: string;
  role: ChatRole;
  content: string;
  sources?: Source[];
  isError?: boolean;
  upload?: UploadState;
}

from typing import Optional

from pydantic import BaseModel


class ChatQueryRequest(BaseModel):
    query: str
    # Omit to start a new conversation; the server creates one and returns
    # its id. History for an existing conversation is loaded server-side
    # from conversation_store — a client no longer supplies it, so it can't
    # be spoofed or dropped by a buggy/malicious frontend.
    conversation_id: Optional[str] = None


class SourceCitation(BaseModel):
    chunk_id: str
    doc_name: str
    excerpt: str

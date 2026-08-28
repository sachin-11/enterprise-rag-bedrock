from typing import Any, Optional

from pydantic import BaseModel


class SyncKBRequest(BaseModel):
    kb_id: Optional[str] = None
    data_source_id: Optional[str] = None


class SyncKBResponse(BaseModel):
    knowledge_base_id: str
    data_source_id: str
    ingestion_job_id: str
    status: str
    statistics: Optional[dict[str, Any]] = None
    failure_reasons: Optional[list[str]] = None

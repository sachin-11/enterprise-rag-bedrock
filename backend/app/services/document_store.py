from app.models.document import DocumentMetadata

_documents: dict[str, DocumentMetadata] = {}


def save_document(metadata: DocumentMetadata) -> None:
    _documents[metadata.document_id] = metadata


def get_document(document_id: str) -> DocumentMetadata | None:
    return _documents.get(document_id)


def delete_document(document_id: str) -> DocumentMetadata | None:
    return _documents.pop(document_id, None)

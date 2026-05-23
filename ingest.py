import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Any

from langchain_chroma import Chroma
from langchain_classic.storage import LocalFileStore
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from pypdf import PdfReader
from dotenv import load_dotenv
# Load environment variables from .env file
load_dotenv()


class SafeChroma(Chroma):
    """Chroma wrapper that tolerates occasional embedding batch mismatches."""

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: list[dict] | None = None,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        # Pre-filter empty/whitespace-only texts to avoid provider-side dropping.
        text_list = list(texts)
        keep_indices = [i for i, text in enumerate(text_list) if text and text.strip()]

        if len(keep_indices) != len(text_list):
            text_list = [text_list[i] for i in keep_indices]
            if metadatas is not None:
                metadatas = [metadatas[i] for i in keep_indices if i < len(metadatas)]
            if ids is not None:
                ids = [ids[i] for i in keep_indices if i < len(ids)]

        if not text_list:
            return []

        try:
            return super().add_texts(text_list, metadatas=metadatas, ids=ids, **kwargs)
        except IndexError:
            # Fallback path for embedding providers that return fewer vectors than inputs.
            added_ids: list[str] = []
            for idx, text in enumerate(text_list):
                single_metadata = [metadatas[idx]] if metadatas is not None and idx < len(metadatas) else None
                single_id = [ids[idx]] if ids is not None and idx < len(ids) else None
                added_ids.extend(
                    super().add_texts([text], metadatas=single_metadata, ids=single_id, **kwargs)
                )
            return added_ids


INGEST_MARKER_FILENAME = ".ingest_complete"


def _marker_path(persist_dir: str) -> Path:
    return Path(persist_dir) / INGEST_MARKER_FILENAME


def _write_ingest_marker(persist_dir: str, payload: dict) -> None:
    marker = _marker_path(persist_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(payload, indent=2))


def _reset_persistence(persist_dir: str, store_dir: str) -> None:
    """Wipe both stores so a partial ingestion can be redone cleanly."""
    for path in (persist_dir, store_dir):
        if os.path.isdir(path):
            shutil.rmtree(path)


def get_parent_retriever(
        persist_dir: str = "./croma_db",
        store_dir: str = "./parent_doc_store"
) -> ParentDocumentRetriever:

    """
    Initializes the hierarchical retriever by connecting 
    to the vector database and parent document store.

    Args:
        persist_dir (str): The directory where the vector database is persisted.
        store_dir (str): The directory where the parent document store is located.

    """
    embedding_model = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
    google_api_key = os.getenv("GOOGLE_API_KEY")
    print(f"Google API Key: {'Provided' if google_api_key else 'Not Provided'}")

    if google_api_key:
        embeddings = GoogleGenerativeAIEmbeddings(
            model=embedding_model,
            google_api_key=google_api_key,
        )
    else:
        embeddings = GoogleGenerativeAIEmbeddings(model=embedding_model)

    vector_store = SafeChroma(
        collection_name="mexican_revolution_vt",
        embedding_function=embeddings,
        persist_directory=persist_dir,
    )

    # Local persistent store for parent documents
    _byte_store = LocalFileStore(store_dir)

    # Partitioning strategy for hierarchical retrieval
    parent_splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)

    return ParentDocumentRetriever(
        vectorstore=vector_store,
        byte_store=_byte_store,
        child_splitter=child_splitter,
        parent_splitter=parent_splitter
    )


def load_pdf_documents(file_path: str) -> list[Document]:
    """Load each PDF page as a LangChain Document with page metadata."""
    reader = PdfReader(file_path)
    docs: list[Document] = []

    for index, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if not page_text:
            continue

        docs.append(
            Document(
                page_content=page_text,
                metadata={
                    "page": index,
                    "total_pages": len(reader.pages),
                },
            )
        )

    return docs


def ingest_document(
    file_path: str,
    persist_dir: str = "./croma_db",
    store_dir: str = "./parent_doc_store",
) -> None:
    """
    Loads a document from the given file path, inject the metadata,
    and store the content in the vector database.

    Idempotency contract:
    - A successful ingestion writes a completion marker file. Subsequent runs
      that see the marker skip re-ingestion.
    - If chunks exist in the vector store but the marker is missing, the
      previous run was interrupted mid-batch. We wipe both stores and
      re-ingest from scratch to avoid leaving a partial corpus in place.
    """
    print(f"Ingesting document: {file_path}")

    docs = load_pdf_documents(file_path)

    if not docs:
        raise ValueError(f"No textual content found to ingest in: {file_path}")

    # RBAC metadata injection
    for doc in docs:
        doc.metadata["source"] = os.path.basename(file_path)
        doc.metadata["ingested_at"] = datetime.now(timezone.utc).isoformat()
        doc.metadata["access_level"] = "internal_confidential"  # Example access level, adjust as needed

    # Inspect persistence state BEFORE opening a Chroma client. Opening one and
    # then deleting the SQLite file underneath leaves the client with a stale
    # readonly handle, which breaks the subsequent re-ingestion.
    marker_exists = _marker_path(persist_dir).is_file()
    chroma_initialized = os.path.isdir(persist_dir) and any(
        name != INGEST_MARKER_FILENAME for name in os.listdir(persist_dir)
    ) if os.path.isdir(persist_dir) else False

    if marker_exists and chroma_initialized:
        retriever = get_parent_retriever(persist_dir=persist_dir, store_dir=store_dir)
        existing_count = retriever.vectorstore._collection.count()
        print(
            f"Collection 'mexican_revolution_vt' already has {existing_count} chunks "
            f"and completion marker is present. Skipping re-ingestion of {file_path}."
        )
        return

    if chroma_initialized and not marker_exists:
        print(
            "Detected Chroma persistence without completion marker — "
            "previous ingestion was interrupted. Wiping stores and re-ingesting."
        )
        _reset_persistence(persist_dir, store_dir)

    retriever = get_parent_retriever(persist_dir=persist_dir, store_dir=store_dir)
    retriever.add_documents(docs)

    _write_ingest_marker(
        persist_dir,
        {
            "source": os.path.basename(file_path),
            "pages": len(docs),
            "chunks": retriever.vectorstore._collection.count(),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    print(f"Document ingested successfully: {file_path}. Total pages: {len(docs)}")


if __name__ == "__main__":
    # Example usage
    INPUT_FILE_PATH = "raw_docs/Sr AI Eng_Challenge_Doc.pdf"
    ingest_document(INPUT_FILE_PATH)

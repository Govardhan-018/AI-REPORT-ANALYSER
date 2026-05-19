# ComplianceAI — Technical Documentation

**Version**: 1.1.1  
**Last Updated**: May 18, 2026  
**Status**: Updated

---

## Executive Summary

ComplianceAI is a local-first desktop application for compliance professionals that combines document ingestion, semantic search, and compliance-aware LLM assistance. The solution uses a FastAPI backend and Electron/React frontend, with primary local LLM inference powered by Ollama and persistent vector storage provided by ChromaDB.

**Key characteristics:**
- 🔒 Local-first design: fast inference on local models
- 📄 Evidence traceability: source citations for responses
- 🧠 Compliance-oriented: built around a RAG pipeline and questionnaire processing
- 🌐 Optional provider fallback: OpenRouter and Groq clients are available only if configured
- 🖥️ Desktop wrapper: Electron frontend with Vite/React

---

## System Architecture

### 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Electron Desktop App                     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              React Frontend (Vite)                   │  │
│  │  ┌─────────────┬─────────────┬──────────────────┐   │  │
│  │  │ Chat Panel  │ Document    │ Evidence Panel   │   │  │
│  │  │             │ Panel       │                  │   │  │
│  │  └─────────────┴─────────────┴──────────────────┘   │  │
│  │                            ↕ (HTTP)                         │
│  │  ┌──────────────────────────────────────────────────────┐  │
│  │  │         FastAPI Backend (localhost:8000)            │  │
│  │  │  ┌──────────────┬──────────┬────────────────────┐   │  │
│  │  │  │ Upload Route │ Chat     │ Document Routes    │   │  │
│  │  │  │              │ Route    │                    │   │  │
│  │  │  └──────────────┴──────────┴────────────────────┘   │  │
│  │  │                      ↕                               │  │
│  │  │  ┌──────────────────────────────────────────────┐   │  │
│  │  │  │       RAG Pipeline Orchestrator             │   │  │
│  │  │  │  (Query → Embed → Search → LLM Inference)   │   │  │
│  │  │  └──────────────────────────────────────────────┘   │  │
│  │  └──────────────────────────────────────────────────────┘  │
│  └─────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────┘
           ↕ (Local subprocess calls)
┌─────────────────────────────────────────────────────────────┐
│                   Ollama Service (Local)                    │
│  ┌──────────────────┬─────────────────────────────────┐   │
│  │ LLM: qwen2.5:7b  │ Embeddings: nomic-embed-text   │   │
│  └──────────────────┴─────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
           ↕ (File system I/O)
┌─────────────────────────────────────────────────────────────┐
│                  Local Storage                              │
│  ┌──────────────────┬──────────────────────────────────┐   │
│  │ ChromaDB Vector  │ Uploaded PDFs & Metadata        │   │
│  │ Database         │                                  │   │
│  └──────────────────┴──────────────────────────────────┘   │  │
└─────────────────────────────────────────────────────────────┘
```

### 2. Technology Stack

| Layer          | Technology              | Purpose                          |
|----------------|-------------------------|----------------------------------|
| **Desktop**    | Electron                | Native desktop app shell         |
| **Frontend**   | React + Vite            | UI and client logic              |
| **Styling**    | TailwindCSS             | Utility styling                  |
| **Backend**    | FastAPI                 | REST / SSE API                   |
| **LLM**        | Ollama                  | Local model inference            |
| **Embeddings** | Ollama / nomic-embed    | Semantic embeddings              |
| **Vector DB**  | ChromaDB                | Local persistent retrieval store |
| **Parsing**    | PyMuPDF, pdfplumber, openpyxl | PDF and Excel document parsing |
| **Async I/O**  | asyncio + httpx         | Concurrency and HTTP clients     |

---

## Backend Architecture

### 3.1 Repository Structure

```
backend/
├── main.py                    # FastAPI app entrypoint
├── requirements.txt           # Python dependencies
├── test_excel_processor.py    # Unit tests for Excel parsing
├── models/
│   └── schemas.py             # Pydantic request/response models
├── routes/
│   ├── upload.py              # PDF ingestion endpoints
│   ├── chat.py                # /api/chat SSE streaming
│   ├── documents.py           # document listing / deletion
│   └── questionnaire.py       # questionnaire upload/run/download
├── services/
│   ├── pdf_processor.py       # PDF extraction
│   ├── excel_processor.py     # Questionnaire parsing & export
│   ├── chunker.py             # Semantic chunk creation
│   ├── embeddings.py          # Embedding utilities
│   ├── vector_store.py        # ChromaDB wrapper
│   ├── rag_pipeline.py        # RAG orchestration
│   ├── batch_rag.py           # Questionnaire batch processing
│   ├── prompt_builder.py      # Prompt construction
│   ├── compliance_intelligence.py # Compliance reasoning logic
│   ├── evidence_analyzer.py   # Evidence scoring
│   ├── response_normalizer.py # Output normalization
│   ├── ollama_client.py       # Local Ollama client
│   ├── openrouter_client.py   # Optional OpenRouter fallback
│   └── groq_client.py         # Optional Groq fallback
└── storage/
    ├── uploads/               # Temporary PDF uploads
    ├── questionnaires/        # Stored questionnaire files
    └── chroma_db/             # ChromaDB persistence
```

### 3.2 Core Backend Services

#### RAG Pipeline (`services/rag_pipeline.py`)

Manages the query flow:
- embed query
- search vector store
- analyze evidence quality
- build prompt
- stream tokens from the chosen LLM
- normalize final response

#### Vector Store (`services/vector_store.py`)

Wraps ChromaDB for:
- storing chunk embeddings
- search by cosine similarity
- document deletion
- document listing

#### PDF Processor (`services/pdf_processor.py`)

Extracts page text from uploaded PDFs, preparing it for chunking and embedding.

#### Excel Processor (`services/excel_processor.py`)

Parses uploaded questionnaires and writes results back to Excel with summary metadata.

#### Batch RAG Processor (`services/batch_rag.py`)

Processes questionnaire questions one-by-one with timeout handling and progress callbacks.

#### LLM Clients

- `OllamaClient` — local inference
- `OpenRouterClient` — optional cloud fallback
- `GroqClient` — optional cloud fallback

---

## API Specification

### 4.1 GET /api/health

Check service health.

**Response:**
```json
{
  "backend": true,
  "ollama": true,
  "ollama_models": ["llama3.2:latest"],
  "chromadb": true,
  "message": "All systems operational."
}
```

### 4.2 GET /api/models

Return available providers and model lists.

**Response:**
```json
{
  "providers": {
    "ollama": { "available": true, "models": ["llama3.2:latest"] },
    "groq": { "available": false, "models": ["llama-3.3-70b-versatile"] },
    "openrouter": { "available": false, "models": ["minimax/minimax-01", "anthropic/claude-3-haiku"] }
  }
}
```

### 4.3 POST /api/upload

Ingest a PDF document and begin processing. Only PDF files are accepted.

**Request:** multipart/form-data with `file`.

**Response:**
```json
{
  "filename": "SOC2_audit.pdf",
  "status": "processing",
  "message": "Document uploaded and processing started."
}
```

### 4.4 GET /api/upload/status/{filename}

Poll the processing status for an uploaded document.

**Response:**
```json
{
  "status": "processing",
  "message": "Extracting text from document...",
  "progress": 40,
  "page_count": 12,
  "chunk_count": 84,
  "uploaded_at": "2026-05-18T12:34:56",
  "file_size": 1456789
}
```

### 4.5 GET /api/documents

List documents present in the vector store and any uploads still processing.

**Response:**
```json
{
  "documents": [
    {
      "filename": "SOC2_audit.pdf",
      "page_count": 12,
      "chunk_count": 84,
      "uploaded_at": "2026-05-18T12:34:56",
      "status": "ready",
      "file_size": 1456789
    }
  ]
}
```

### 4.6 DELETE /api/documents/{filename}

Delete a document from ChromaDB and the backend upload directory.

**Response:**
```json
{
  "filename": "SOC2_audit.pdf",
  "deleted_chunks": 84,
  "message": "Document 'SOC2_audit.pdf' deleted."
}
```

### 4.7 POST /api/chat

Run a chat query through the RAG pipeline and stream the response back via SSE.

**Request body:**
```json
{
  "query": "What does the retention policy require?",
  "provider": "ollama",
  "model": null,
  "conversation_history": []
}
```

**SSE events:**
- `evidence`
- `token`
- `done`
- `error`

### 4.8 POST /questionnaire/upload

Upload a questionnaire workbook for parsing and preview. Only `.xlsx` is supported.

**Request:** multipart/form-data with `file`.

**Response:**
```json
{
  "file_id": "uuid-1234",
  "filename": "Questionnaire.xlsx",
  "total_rows": 32,
  "detected_columns": ["No","Question","Answer","Status","Evidence"],
  "preview_rows": [ ... ]
}
```

### 4.9 POST /questionnaire/run

Run a questionnaire file through batch RAG processing.

**Request body:**
```json
{
  "file_id": "uuid-1234",
  "question_column": "Question",
  "answer_column": "Answer",
  "status_column": "Status",
  "evidence_column": "Evidence",
  "page_column": "Page",
  "confidence_column": "Confidence",
  "provider": "ollama",
  "model": null
}
```

### 4.10 GET /questionnaire/download/{download_token}

Download a completed questionnaire result file. Tokens expire after one hour.

---

## Data Flow & Processing Pipeline

### 5.1 Document Ingestion Flow

```
Upload PDF
   ↓
Extract pages & text
   ↓
Chunk text
   ↓
Generate embeddings
   ↓
Store chunks in ChromaDB
```

### 5.2 Questionnaire Flow

```
Upload .xlsx questionnaire
   ↓
Detect headers and question rows
   ↓
Store file metadata in registry
   ↓
Process each question via RAG
   ↓
Write answers back to Excel output
   ↓
Issue download token
```

### 5.3 Query Execution Flow

```
User query
   ↓
Embed query
   ↓
Vector search
   ↓
Evidence analysis
   ↓
Prompt build
   ↓
LLM stream
   ↓
Normalize answer
```

### 5.4 Evidence Traceability

Responses include:
- source filename
- page number
- relevance score
- chunk excerpt
- evidence strength / gap analysis

---

## Security & Privacy

### 6.1 Local Binding

`backend/main.py` binds to `127.0.0.1` only. The app is not exposed publicly by default.

### 6.2 Local storage

- PDFs: `backend/storage/uploads/`
- Questionnaires: `backend/storage/questionnaires/`
- Vectors: `backend/storage/chroma_db/`

### 6.3 Optional cloud providers

- OpenRouter and Groq are available only when their API keys exist.
- Core operation remains local if only Ollama is configured.

### 6.4 Known gaps

Not yet implemented:
- authentication and RBAC
- audit log persistence
- encryption at rest
- rate limiting
- document access controls

---

## Deployment & Operations

### 7.1 System Requirements

- Python 3.10+
- Node.js 18+
- Ollama installed and running locally
- `nomic-embed-text` model present in Ollama
- Optional local model: `qwen2.5:7b`

### 7.2 Setup

```bash
cd backend
pip install -r requirements.txt

cd frontend
npm install
```

### 7.3 Startup

**Python script:**
```bash
python start.py
```

**Manual backend:**
```bash
cd backend && uvicorn main:app --host 127.0.0.1 --port 8000
```

**Manual frontend:**
```bash
cd frontend && npm run electron
```

---

## Limitations

- `/api/upload` currently accepts only PDF documents.
- Questionnaire upload only accepts `.xlsx` files.
- No Word or generic CSV upload support in the current implementation.
- SSE stream handling is assumed by the frontend and does not yet support malformed streams.

---

## Extensibility & Roadmap

### 8.1 Extension points

- add new document parsers in `backend/services`
- add new LLM clients by extending existing wrappers
- replace ChromaDB with alternate vector stores
- expand questionnaire output formats

### 8.2 Future enhancements

- authentication and audit logging
- export reports with evidence citations
- query caching and reranking
- multi-user namespaces and permissions
- improved model selection UI

---

## Contribution Notes

- Keep route models aligned with `backend/models/schemas.py`.
- Update frontend dropdowns when `/api/models` changes.
- Maintain SSE event contract in `frontend/src/panels/ChatPanel.jsx` and `frontend/src/panels/QuestionnairePanel.jsx`.

---

**Document Version**: 1.1.1
**Last Updated**: May 18, 2026
**Status**: Updated to reflect current code and API behavior.

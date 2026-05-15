# ComplianceAI — Local AI Compliance Document Assistant

Offline-first, air-gap-compatible desktop application for compliance professionals to query sensitive documents (ISO 27001, SOC 2, vendor assessments, audit evidence) using **local AI** — with **zero cloud dependency**, **zero data transmission**, and **strict evidence traceability**.

## Architecture

| Layer       | Technology                     |
|-------------|-------------------------------|
| Desktop     | Electron                      |
| Frontend    | React + TailwindCSS (Vite)    |
| Backend     | Python FastAPI (localhost)    |
| LLM         | Ollama → qwen2.5:7b          |
| Embeddings  | Ollama → nomic-embed-text     |
| Vector DB   | ChromaDB (local persistent)   |
| PDF parsing | PyMuPDF + pdfplumber          |

## Prerequisites

1. **Ollama** installed and running:
   ```
   ollama serve
   ```

2. **Models** pulled:
   ```
   ollama pull qwen2.5:7b
   ollama pull nomic-embed-text
   ```

3. **Node.js** 18+ and **Python** 3.10+

## Setup & Run

The easiest way to run the application is to use one of the provided launcher scripts in the root directory. This will start both the backend and frontend simultaneously.

### Easy Launch (Recommended)

**Option 1: Windows Batch File**
Simply double-click `start.bat` in File Explorer, or run it from the command line:
```bash
.\start.bat
```
This will open two separate terminal windows for the frontend and backend.

**Option 2: Python Script**
If you prefer running everything in a single terminal window:
```bash
python start.py
```
You can stop both services by pressing `Ctrl + C` in that terminal.

### Manual Launch (Advanced)

If you need to run them separately:

#### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

#### Frontend (Development)
```bash
cd frontend
npm install
npm run dev          # React dev server at http://localhost:5173
```

#### Electron Desktop App
```bash
cd frontend
npm run electron     # Launches Vite + Electron together
```

## Security

- FastAPI bound to `127.0.0.1` only — never `0.0.0.0`
- Electron: `contextIsolation=true`, `nodeIntegration=false`, `sandbox=true`
- No external fetch in renderer process
- Uploaded files auto-deleted after indexing
- No logging of document content to disk
- No telemetry, no analytics, no hidden pings

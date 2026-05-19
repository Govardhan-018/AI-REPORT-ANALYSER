"""
ComplianceAI Backend — FastAPI Application Entry Point

Fully local, offline-first backend for AI-powered compliance document analysis.
No external network calls. No telemetry. No cloud APIs.
Bound to 127.0.0.1 only — never 0.0.0.0.
"""

import sys
import os
import asyncio

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.upload import router as upload_router
from routes.chat import router as chat_router
from routes.documents import router as documents_router
from routes.questionnaire import router as questionnaire_router
from routes.questionnaire import cleanup_old_questionnaires, periodic_cleanup
from services.vector_store import VectorStoreService
from services.ollama_client import OllamaClient
from services.openrouter_client import OpenRouterClient
from services.groq_client import GroqClient
from models.schemas import HealthResponse

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("complianceai")

# Load environment variables
load_dotenv()

# Paths
BASE_DIR = os.path.dirname(__file__)
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
CHROMA_DIR = os.path.join(STORAGE_DIR, "chroma_db")
QUESTIONNAIRE_DIR = os.path.join(STORAGE_DIR, "questionnaires")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(QUESTIONNAIRE_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize services on startup, cleanup on shutdown."""
    logger.info("Starting ComplianceAI backend...")

    # Initialize ChromaDB
    try:
        vector_store = VectorStoreService(persist_directory=CHROMA_DIR)
        app.state.vector_store = vector_store
        logger.info("ChromaDB initialized at %s", CHROMA_DIR)
    except Exception as e:
        logger.error("Failed to initialize ChromaDB: %s", e)
        app.state.vector_store = None

    # Check Ollama availability
    ollama_client = OllamaClient()
    app.state.ollama_client = ollama_client
    is_available = await ollama_client.check_availability()
    if is_available:
        logger.info("Ollama is available")
        models = await ollama_client.list_models()
        logger.info("Available models: %s", models)
    else:
        logger.warning("Ollama is not available — start Ollama before using chat")

    # Initialize OpenRouter Client
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_client = OpenRouterClient(api_key=openrouter_api_key)
    app.state.openrouter_client = openrouter_client
    if openrouter_api_key:
        logger.info("OpenRouter is available")
    else:
        logger.warning("OpenRouter is not available — OPENROUTER_API_KEY not set")

    # Initialize Groq Client
    groq_api_key = os.getenv("GROQ_API_KEY", "")
    groq_client = GroqClient(api_key=groq_api_key)
    app.state.groq_client = groq_client
    if groq_api_key:
        logger.info("Groq is available")
    else:
        logger.warning("Groq is not available — GROQ_API_KEY not set")

    # Store paths in app state
    app.state.uploads_dir = UPLOADS_DIR
    app.state.chroma_dir = CHROMA_DIR
    app.state.processing_status = {}

    # Questionnaire state
    app.state.questionnaire_registry = {}
    app.state.download_tokens = {}

    # Run initial cleanup and start periodic cleanup task
    await cleanup_old_questionnaires()
    cleanup_task = asyncio.create_task(periodic_cleanup())

    logger.info("ComplianceAI backend ready on http://127.0.0.1:8000")
    yield

    # Cancel cleanup on shutdown
    cleanup_task.cancel()
    logger.info("Shutting down ComplianceAI backend...")


# Create FastAPI application
app = FastAPI(
    title="ComplianceAI",
    description="Local AI-powered compliance document assistant",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — localhost only
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(upload_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(questionnaire_router, prefix="/questionnaire")


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Check system health: backend, Ollama, ChromaDB status."""
    ollama_client: OllamaClient = app.state.ollama_client
    ollama_ok = await ollama_client.check_availability()
    models = await ollama_client.list_models() if ollama_ok else []
    chromadb_ok = app.state.vector_store is not None

    parts = []
    if not ollama_ok:
        parts.append("Ollama is not running.")
    if not chromadb_ok:
        parts.append("ChromaDB failed to initialize.")
    if not parts:
        parts.append("All systems operational.")

    return HealthResponse(
        backend=True,
        ollama=ollama_ok,
        ollama_models=models,
        chromadb=chromadb_ok,
        message=" ".join(parts),
    )


@app.get("/api/models")
async def get_models():
    """Return available providers and their models."""
    ollama_client: OllamaClient = app.state.ollama_client
    ollama_ok = await ollama_client.check_availability()
    ollama_models = await ollama_client.list_models() if ollama_ok else []
    # Filter out embedding models (like nomic-embed-text)
    ollama_models = [m for m in ollama_models if "embed" not in m.lower()]
    
    # We hardcode popular free Groq models
    groq_models = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it"
    ]
    
    return {
        "providers": {
            "ollama": {
                "available": ollama_ok,
                "models": ollama_models
            },
            "groq": {
                "available": app.state.groq_client.api_key != "",
                "models": groq_models
            },
            "openrouter": {
                "available": app.state.openrouter_client.api_key != "",
                "models": ["minimax/minimax-01", "anthropic/claude-3-haiku"] # Sample default models
            }
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

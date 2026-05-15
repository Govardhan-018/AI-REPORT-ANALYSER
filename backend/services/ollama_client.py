"""
Ollama Client — Local LLM interface for chat and model management.

Connects to Ollama running on localhost:11434.
Supports streaming and non-streaming chat completion.
No external network calls.
"""

import logging
from typing import List, AsyncGenerator, Optional

import ollama

logger = logging.getLogger("complianceai.ollama_client")

DEFAULT_CHAT_MODEL = "llama3.2:latest"


class OllamaClient:
    """Client for interacting with local Ollama instance."""

    def __init__(self, chat_model: str = DEFAULT_CHAT_MODEL):
        self.chat_model = chat_model
        self._client = ollama.AsyncClient()

    async def check_availability(self) -> bool:
        try:
            await self._client.list()
            return True
        except Exception as e:
            logger.warning("Ollama not available: %s", e)
            return False

    async def check_model(self, model: str) -> bool:
        try:
            models = await self.list_models()
            return model in models
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        try:
            response = await self._client.list()
            models = []
            if hasattr(response, "models"):
                for model in response.models:
                    models.append(model.model)
            return models
        except Exception as e:
            logger.error("Failed to list models: %s", e)
            return []

    async def chat_stream(self, messages: List[dict], model: Optional[str] = None) -> AsyncGenerator[str, None]:
        model = model or self.chat_model
        async for token in self._stream_generator(messages, model):
            yield token

    async def _stream_generator(self, messages: List[dict], model: str) -> AsyncGenerator[str, None]:
        try:
            stream = await self._client.chat(model=model, messages=messages, stream=True)
            token_count = 0
            async for chunk in stream:
                if hasattr(chunk, "message") and hasattr(chunk.message, "content"):
                    content = chunk.message.content
                    if content:
                        token_count += 1
                        yield content
                elif isinstance(chunk, dict):
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        token_count += 1
                        yield content
            logger.info("Stream completed. Total tokens: %d", token_count)
        except Exception as e:
            logger.error("Streaming chat failed: %s", e, exc_info=True)
            yield f"\n\n[Error: Failed to get response from Ollama. Ensure Ollama is running with {model} model.]"

    async def chat(self, messages: List[dict], model: Optional[str] = None) -> str:
        model = model or self.chat_model
        try:
            response = await self._client.chat(model=model, messages=messages, stream=False)
            if hasattr(response, "message"):
                return response.message.content
            elif isinstance(response, dict):
                return response.get("message", {}).get("content", "")
            return ""
        except Exception as e:
            logger.error("Chat failed: %s", e)
            return f"[Error: Failed to get response from Ollama. Ensure {model} is available.]"

"""
Groq Client — Cloud LLM interface for fast inference.

Connects to Groq API.
Supports streaming and non-streaming chat completion.
"""

import logging
from typing import List, AsyncGenerator, Optional
from groq import AsyncGroq

logger = logging.getLogger("complianceai.groq_client")

DEFAULT_CHAT_MODEL = "llama-3.3-70b-versatile"

class GroqClient:
    """Client for interacting with Groq API."""

    def __init__(self, api_key: str, chat_model: str = DEFAULT_CHAT_MODEL):
        self.chat_model = chat_model
        self.api_key = api_key
        if self.api_key:
            self._client = AsyncGroq(api_key=self.api_key)
        else:
            self._client = None

    async def check_availability(self) -> bool:
        if not self._client:
            logger.warning("Groq not available: GROQ_API_KEY not set")
            return False
        return True

    async def chat_stream(self, messages: List[dict], model: Optional[str] = None) -> AsyncGenerator[str, None]:
        model = model or self.chat_model
        if not self._client:
            yield f"\n\n[Error: GROQ_API_KEY is not configured. Please set it in your .env file.]"
            return
            
        token_count = 0
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
            )
            async for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    token_count += 1
                    yield content
                    
            logger.info("Groq stream completed. Total tokens: %d", token_count)
        except Exception as e:
            logger.error("Groq streaming chat failed: %s", e, exc_info=True)
            yield f"\n\n[Error: Failed to get response from Groq: {str(e)}]"

    async def chat(self, messages: List[dict], model: Optional[str] = None) -> str:
        model = model or self.chat_model
        if not self._client:
            return "[Error: GROQ_API_KEY is not configured. Please set it in your .env file.]"
            
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error("Groq chat failed: %s", e)
            return f"[Error: Failed to get response from Groq: {str(e)}]"

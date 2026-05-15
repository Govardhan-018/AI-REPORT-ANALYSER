"""
OpenRouter Client — Cloud LLM interface for chat.

Connects to OpenRouter API.
Supports streaming and non-streaming chat completion.
"""

import json
import logging
import httpx
from typing import List, AsyncGenerator, Optional

logger = logging.getLogger("complianceai.openrouter_client")

DEFAULT_CHAT_MODEL = "minimax/minimax-01"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

class OpenRouterClient:
    """Client for interacting with OpenRouter API."""

    def __init__(self, api_key: str, chat_model: str = DEFAULT_CHAT_MODEL):
        self.chat_model = chat_model
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "ComplianceAI Local",
        }

    async def check_availability(self) -> bool:
        if not self.api_key:
            logger.warning("OpenRouter not available: OPENROUTER_API_KEY not set")
            return False
        # Minimal check: we just rely on API key existence for availability
        return True

    async def chat_stream(self, messages: List[dict], model: Optional[str] = None) -> AsyncGenerator[str, None]:
        model = model or self.chat_model
        if not self.api_key:
            yield f"\n\n[Error: OPENROUTER_API_KEY is not configured.]"
            return
            
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        
        token_count = 0
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", OPENROUTER_URL, headers=self.headers, json=payload, timeout=60.0) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        logger.error("OpenRouter streaming chat failed: %s - %s", response.status_code, error_text)
                        yield f"\n\n[Error: OpenRouter API returned status {response.status_code}.]"
                        return

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        token_count += 1
                                        yield content
                            except json.JSONDecodeError:
                                pass
                                
            logger.info("OpenRouter stream completed. Total tokens: %d", token_count)
        except Exception as e:
            logger.error("OpenRouter streaming chat failed: %s", e, exc_info=True)
            yield f"\n\n[Error: Failed to get response from OpenRouter: {str(e)}]"

    async def chat(self, messages: List[dict], model: Optional[str] = None) -> str:
        model = model or self.chat_model
        if not self.api_key:
            return "[Error: OPENROUTER_API_KEY is not configured.]"
            
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(OPENROUTER_URL, headers=self.headers, json=payload, timeout=120.0)
                response.raise_for_status()
                data = response.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return ""
        except Exception as e:
            logger.error("OpenRouter chat failed: %s", e)
            return f"[Error: Failed to get response from OpenRouter: {str(e)}]"

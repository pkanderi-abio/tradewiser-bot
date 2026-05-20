"""
LLM Service — Multi-provider AI backend for trading analysis.

Supports:
  - OpenAI (GPT-4, GPT-3.5)
  - Anthropic Claude (Claude 3 Opus, Sonnet)
  - Groq (Llama 3.3-70B, free tier)
  - Local Ollama (Llama 3.2, free)

Priority order:
  1. OpenAI (if OPENAI_API_KEY set)
  2. Anthropic (if ANTHROPIC_API_KEY set)
  3. Groq (if GROQ_API_KEY set)
  4. Ollama (local, always available)
"""

import json
import time
from typing import Dict, List, Optional, Tuple
from enum import Enum
import asyncio

from app.core.config import settings
from app.core.logger import logger

# LLM Provider Enum
class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"
    OLLAMA = "ollama"

# Model configs
LLM_CONFIGS = {
    LLMProvider.OPENAI: {
        "models": ["gpt-4", "gpt-4-turbo-preview", "gpt-3.5-turbo"],
        "default": "gpt-4-turbo-preview",
        "base_url": "https://api.openai.com/v1",
    },
    LLMProvider.ANTHROPIC: {
        "models": ["claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"],
        "default": "claude-3-opus-20240229",
        "base_url": "https://api.anthropic.com",
    },
    LLMProvider.GROQ: {
        "models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
        "default": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
    },
    LLMProvider.OLLAMA: {
        "models": ["llama3.2", "llama2", "mistral"],
        "default": "llama3.2",
        "base_url": "http://localhost:11434/v1",
    },
}


class LLMService:
    """Multi-provider LLM service for trading analysis."""

    def __init__(self):
        self._client = None
        self._provider = self._detect_provider()
        self._model = self._get_model()
        self._response_cache: Dict[str, Tuple[float, str]] = {}
        self._cache_ttl = 3600  # 1 hour default

    def _detect_provider(self) -> LLMProvider:
        """Detect available LLM provider in priority order."""
        if settings.OPENAI_API_KEY:
            logger.info("Using OpenAI as LLM provider")
            return LLMProvider.OPENAI
        elif settings.ANTHROPIC_API_KEY:
            logger.info("Using Anthropic Claude as LLM provider")
            return LLMProvider.ANTHROPIC
        elif settings.GROQ_API_KEY:
            logger.info("Using Groq as LLM provider")
            return LLMProvider.GROQ
        else:
            logger.info("Using local Ollama as LLM provider")
            return LLMProvider.OLLAMA

    def _get_model(self) -> str:
        """Get configured model for the current provider."""
        config = LLM_CONFIGS[self._provider]
        if self._provider == LLMProvider.OPENAI:
            return settings.OPENAI_MODEL or config["default"]
        elif self._provider == LLMProvider.ANTHROPIC:
            return settings.ANTHROPIC_MODEL or config["default"]
        elif self._provider == LLMProvider.GROQ:
            return settings.GROQ_MODEL or config["default"]
        else:
            return settings.OLLAMA_MODEL or config["default"]

    def _get_client(self):
        """Get or create LLM client for current provider."""
        if self._client is not None:
            return self._client

        try:
            if self._provider == LLMProvider.OPENAI:
                from openai import OpenAI
                self._client = OpenAI(api_key=settings.OPENAI_API_KEY)
            elif self._provider == LLMProvider.ANTHROPIC:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            elif self._provider == LLMProvider.GROQ:
                from groq import Groq
                self._client = Groq(api_key=settings.GROQ_API_KEY)
            elif self._provider == LLMProvider.OLLAMA:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url="http://localhost:11434/v1",
                    api_key="ollama",
                )
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
            raise

        return self._client

    def get_provider(self) -> str:
        """Get current LLM provider name."""
        return self._provider.value

    def get_model(self) -> str:
        """Get current LLM model name."""
        return self._model

    def get_capabilities(self) -> dict:
        """Get capabilities of current LLM provider."""
        return {
            "provider": self.get_provider(),
            "model": self.get_model(),
            "models_available": LLM_CONFIGS[self._provider]["models"],
            "max_tokens": 4096,
            "streaming": True,
        }

    def query(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        cache_key: Optional[str] = None,
    ) -> str:
        """
        Query the LLM with given prompt.
        
        Args:
            prompt: User prompt
            system_prompt: System prompt for context
            temperature: 0-1, higher = more creative
            max_tokens: Maximum response tokens
            cache_key: Optional key for caching response
        
        Returns:
            LLM response text
        """
        # Check cache
        if cache_key and cache_key in self._response_cache:
            ts, response = self._response_cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                logger.debug(f"Cache hit for key: {cache_key}")
                return response

        try:
            client = self._get_client()
            messages = []
            
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            
            messages.append({"role": "user", "content": prompt})

            if self._provider == LLMProvider.ANTHROPIC:
                response = client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=system_prompt or "",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                )
                result = response.content[0].text
            else:
                response = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                result = response.choices[0].message.content

            # Cache result
            if cache_key:
                self._response_cache[cache_key] = (time.time(), result)

            return result

        except Exception as e:
            logger.error(f"LLM query failed: {e}")
            raise

    async def query_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """Async query wrapper for concurrent operations."""
        return await asyncio.to_thread(
            self.query,
            prompt,
            system_prompt,
            temperature,
            max_tokens,
        )

    def parse_json_response(self, response: str) -> dict:
        """Extract and parse JSON from LLM response."""
        try:
            # Try direct JSON parse
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            import re
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"Could not parse JSON from response: {response}")

    def set_cache_ttl(self, ttl_seconds: int):
        """Set cache TTL for LLM responses."""
        self._cache_ttl = ttl_seconds

    def clear_cache(self):
        """Clear response cache."""
        self._response_cache.clear()
        logger.info("LLM response cache cleared")

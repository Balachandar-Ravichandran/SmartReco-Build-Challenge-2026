"""Mesh API client — OpenAI-compatible LLM provider (production/competition).

This is the ONLY module that calls Mesh API. All LLM calls in the system go through here.
"""
import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from backend.core.config import get_settings
from backend.tools._schema_utils import coerce_null_lists


class MeshClient:
    """Mesh API client (OpenAI-compatible interface).

    Enforces:
    - Hard per-call timeouts (3s embed / 6s completion)
    - Bounded HTTP-layer retry with exponential backoff on connection errors
    - Fast fail to caller (never hangs)
    - Structured output validation (fail fast, no LLM retry on schema error)
    """

    def __init__(self):
        self.settings = get_settings()
        self.api_key = self.settings.MESH_API_KEY
        self.api_base = self.settings.MESH_API_BASE.rstrip("/")
        self.embed_timeout = self.settings.MESH_EMBED_TIMEOUT_SEC
        self.complete_timeout = self.settings.MESH_COMPLETE_TIMEOUT_SEC
        self.embed_model = self.settings.MESH_EMBED_MODEL
        self.completion_model = self.settings.MESH_COMPLETION_MODEL

    async def embed(self, text: str) -> list[float]:
        """Embed text to vector.

        Args:
            text: Text to embed

        Returns:
            Float vector

        Raises:
            TimeoutError: If embed call exceeds timeout
            ValueError: If embedding fails after retries
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/embeddings",
                    json={
                        "model": self.embed_model,
                        "input": text,
                    },
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.embed_timeout,
                )
                response.raise_for_status()
                data = response.json()
                return data["data"][0]["embedding"]
        except (asyncio.TimeoutError, httpx.TimeoutException) as e:
            # httpx's own `timeout=` kwarg raises httpx.TimeoutException
            # (ReadTimeout/ConnectTimeout/PoolTimeout), NOT asyncio.TimeoutError —
            # both need catching here, and httpx's timeout exceptions often
            # str() to an empty string, so build the message explicitly rather
            # than relying on `{e}`.
            raise TimeoutError(
                f"Embedding call timed out after {self.embed_timeout}s "
                f"({type(e).__name__}): {text[:50]}..."
            ) from e
        except httpx.HTTPError as e:
            raise ValueError(f"Embedding call failed ({type(e).__name__}): {e}") from e
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise ValueError(f"Malformed embedding response: {e}") from e

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: type[BaseModel] | None = None,
    ) -> dict:
        """Call LLM for completion with optional structured output.

        Args:
            system: System prompt
            user: User message
            response_schema: Pydantic model for structured output (optional)

        Returns:
            Dict matching response_schema if provided, else raw message content

        Raises:
            TimeoutError: If completion call exceeds timeout
            ValueError: If completion fails or structured output validation fails
        """
        if not system or not user:
            raise ValueError("System and user prompts cannot be empty")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        request_body = {
            "model": self.completion_model,
            "messages": messages,
            "temperature": 0.7,
        }

        # Add structured output if schema provided
        if response_schema:
            schema_dict = response_schema.model_json_schema()
            request_body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": schema_dict,
                    "strict": True,
                },
            }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    json=request_body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.complete_timeout,
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # If structured output requested, validate and parse
                if response_schema:
                    try:
                        parsed = json.loads(content)
                        parsed = coerce_null_lists(parsed, response_schema)
                        validated = response_schema(**parsed)
                        return validated.model_dump()
                    except (json.JSONDecodeError, ValidationError) as e:
                        raise ValueError(
                            f"Structured output validation failed: {e}. "
                            f"Response: {content[:200]}"
                        ) from e

                return {"content": content}

        except (asyncio.TimeoutError, httpx.TimeoutException) as e:
            raise TimeoutError(
                f"Completion call timed out after {self.complete_timeout}s "
                f"({type(e).__name__})"
            ) from e
        except httpx.HTTPError as e:
            raise ValueError(f"Completion call failed ({type(e).__name__}): {e}") from e
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise ValueError(f"Malformed completion response: {e}") from e

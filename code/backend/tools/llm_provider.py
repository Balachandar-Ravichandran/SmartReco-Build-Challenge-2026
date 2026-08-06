"""Abstract LLM provider interface — Mesh API is the sole provider.

All LLM calls go through this module rather than importing mesh_client.py
directly, so call sites (agent nodes, API routes) never depend on a concrete
client implementation.
"""
from typing import Protocol


class LLMProvider(Protocol):
    """Interface for LLM providers (embedding + completion)."""

    async def embed(self, text: str) -> list[float]:
        """Embed text to vector representation.

        Args:
            text: Text to embed

        Returns:
            Float vector (embeddings)

        Raises:
            TimeoutError: If call exceeds timeout
            ValueError: If embedding fails after retries
        """
        ...

    async def complete(
        self, system: str, user: str, response_schema: type | None = None
    ) -> dict:
        """Call LLM for completion with optional structured output.

        Args:
            system: System prompt
            user: User message
            response_schema: Pydantic model for structured output (optional)

        Returns:
            Dict matching response_schema if provided, else raw response

        Raises:
            TimeoutError: If call exceeds timeout
            ValueError: If completion fails or structured output validation fails
        """
        ...


def get_provider() -> LLMProvider:
    """Factory: return the configured completion provider (Solver's `.complete()` calls)."""
    from backend.tools.mesh_client import MeshClient

    return MeshClient()


def get_embedding_provider() -> LLMProvider:
    """Factory: return the embedding provider.

    Every `.embed()` call in the system (cold start, warm-agent embedder,
    catalog dual-write, reconciliation) goes through this function.
    """
    from backend.tools.mesh_client import MeshClient

    return MeshClient()

import httpx

from config import get_settings

_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIMS = 1536


async def embed(text: str) -> list[float]:
    """Return an embedding vector for `text` (OpenAI text-embedding-3-small, 1536 dims)."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": _EMBEDDING_MODEL, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

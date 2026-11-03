"""Embedding providers for document retrieval."""

import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EmbeddingError(RuntimeError):
    """Raised when a local embedding provider cannot create vectors."""


class NomicOllamaEmbeddings:
    """Use Ollama's local nomic-embed-text model for embeddings."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        model: str | None = None,
    ) -> None:
        ollama_url = os.environ.get("LOCALRAG_OLLAMA_URL", "http://127.0.0.1:11434")
        self.endpoint = endpoint or f"{ollama_url.rstrip('/')}/api/embed"
        self.model = model or os.environ.get(
            "LOCALRAG_EMBEDDING_MODEL", "nomic-embed-text"
        )

    @staticmethod
    def name() -> str:
        """Keep compatibility with collections made by older Chroma clients.

        Older LocalRAG versions created this collection with Chroma's default
        embedding-function identity. Chroma allows an explicitly supplied
        function named ``default`` to open that collection without a conflict.
        """
        return "default"

    def get_config(self) -> dict[str, str]:
        """Return the serializable configuration required by Chroma."""
        return {"endpoint": self.endpoint, "model": self.model}

    @staticmethod
    def build_from_config(config: dict[str, str]) -> "NomicOllamaEmbeddings":
        """Recreate the embedding function saved with a Chroma collection."""
        return NomicOllamaEmbeddings(
            endpoint=config["endpoint"], model=config["model"]
        )

    def is_legacy(self) -> bool:
        """Use Chroma's registered-function configuration format."""
        return False

    def default_space(self) -> str:
        """Use cosine distance for normalized local embedding vectors."""
        return "cosine"

    def supported_spaces(self) -> list[str]:
        return ["cosine", "l2", "ip"]

    def __call__(self, input: list[str]) -> list[list[float]]:
        if not input:
            return []
        if not all(isinstance(text, str) and text.strip() for text in input):
            raise EmbeddingError("Embedding inputs must be non-empty text.")
        payload = json.dumps({"model": self.model, "input": input}).encode("utf-8")
        request = Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
            embeddings = body["embeddings"]
        except (
            HTTPError,
            URLError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as error:
            raise EmbeddingError(
                f"Embedding model '{self.model}' is unavailable at {self.endpoint}. "
                "Check LOCALRAG_OLLAMA_URL and install the model in Ollama, or use the SQLite backend."
            ) from error
        if (
            not isinstance(embeddings, list)
            or len(embeddings) != len(input)
            or not embeddings
            or not all(isinstance(vector, list) and vector for vector in embeddings)
        ):
            raise EmbeddingError("The embedding provider returned an invalid response.")
        dimension = len(embeddings[0])
        if any(
            len(vector) != dimension
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
            for vector in embeddings
        ):
            raise EmbeddingError("The embedding provider returned invalid vector values.")
        return [[float(value) for value in vector] for vector in embeddings]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        """Embed query texts using the same representation as indexed chunks."""
        return self(input)

from __future__ import annotations

from functools import lru_cache
from typing import Protocol, Sequence

import numpy as np

DEFAULT_MODEL = "sentence-transformers/all-mpnet-base-v2"


class Embedder(Protocol):
    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Return L2-normalised float32 vectors, one row per text."""
        ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, batch_size: int = 64) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.batch_size = batch_size
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.ascontiguousarray(vectors, dtype=np.float32)


@lru_cache(maxsize=2)
def get_embedder(model_name: str = DEFAULT_MODEL) -> SentenceTransformerEmbedder:
    """Model loading takes seconds, so share one instance per model name."""
    return SentenceTransformerEmbedder(model_name)

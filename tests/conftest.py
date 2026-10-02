from __future__ import annotations

import re
import zlib

import numpy as np
import pytest

DIM = 512


class HashingEmbedder:
    """Deterministic bag-of-words embedder so tests need no model download."""

    def encode(self, texts):
        vectors = np.zeros((len(texts), DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                vectors[row, zlib.crc32(token.encode()) % DIM] += 1.0
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1.0, norms)


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder()

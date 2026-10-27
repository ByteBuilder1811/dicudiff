"""Local embedding backends. No network access unless ALLOW_MODEL_DOWNLOAD=true.

  sentence-transformers : real semantic embeddings (default model all-MiniLM-L6-v2, Apache-2.0, ~90 MB)
  hashing               : deterministic hashed word-n-gram vectors. LEXICAL, not semantic. Used for tests/offline demos.
  none                  : disable dense retrieval and semantic matching (BM25 + deterministic diff still work)
"""
import hashlib
import logging
import re
import threading

import numpy as np

from app.core.config import get_settings

log = logging.getLogger("docudiff.embeddings")


class HashingEmbedder:
    name = "hashing-384"
    semantic = False
    dim = 384

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            words = re.findall(r"\w+", t.lower())
            feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
            for f in feats:
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                out[r, h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(n, 1e-9)


class SentenceTransformerEmbedder:
    semantic = True

    def __init__(self, model_name: str, allow_download: bool):
        from sentence_transformers import SentenceTransformer  # heavy import, lazy

        self.name = model_name
        self.model = SentenceTransformer(model_name, device="cpu", local_files_only=not allow_download)
        getter = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dim = getter()
        self._encode_lock = threading.Lock()  # one encode at a time: bounded RAM/CPU under concurrent requests

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._encode_lock:
            v = self.model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(v, dtype=np.float32)


_lock = threading.Lock()
_cache: dict = {}


def get_embedder():
    """Returns an embedder or None. The reason for None is available from embedder_status()."""
    with _lock:
        if "obj" in _cache:
            return _cache["obj"]
        s = get_settings()
        backend, obj, reason = s.embedding_backend.lower(), None, ""
        if backend == "hashing":
            obj = HashingEmbedder()
        elif backend in ("auto", "sentence-transformers"):
            try:
                obj = SentenceTransformerEmbedder(s.embedding_model, s.allow_model_download)
            except Exception as exc:
                reason = (f"Embedding model '{s.embedding_model}' is not available locally ({type(exc).__name__}). "
                          "Run `python scripts/download_models.py` (about 90 MB) or set ALLOW_MODEL_DOWNLOAD=true.")
                log.warning(reason)
        else:
            reason = "Embeddings disabled (EMBEDDING_BACKEND=none)."
        _cache.update(obj=obj, reason=reason)
        return obj


def embedder_status() -> dict:
    e = get_embedder()
    with _lock:
        reason = _cache.get("reason", "")
    return {"available": e is not None, "name": getattr(e, "name", None), "semantic": getattr(e, "semantic", False), "reason": reason}


def reset_embedder_cache() -> None:
    with _lock:
        _cache.clear()

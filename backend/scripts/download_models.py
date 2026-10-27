"""Optional setup step: download the local embedding model (~90 MB, Apache-2.0) into the Hugging Face cache.
Nothing else in DocuDiff downloads models. Run:  python scripts/download_models.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings

name = get_settings().embedding_model
print(f"Downloading {name} (~90 MB) to the local Hugging Face cache…")
from sentence_transformers import SentenceTransformer  # noqa: E402

m = SentenceTransformer(name, device="cpu")
print("OK, embedding dimension:", m.get_sentence_embedding_dimension())
sys.exit(0)

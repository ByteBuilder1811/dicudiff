import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="docudiff-test-")
os.environ["DATA_DIR"] = _tmp  # must be set before app.core.config is imported
os.environ["EMBEDDING_BACKEND"] = "hashing"
os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"  # unreachable on purpose: tests must not need an LLM
os.environ["RATE_LIMIT_PER_MINUTE"] = "100000"

import pytest  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    s = get_settings()
    db.init_engine(f"sqlite:///{Path(_tmp) / 'test.db'}")
    db.Base.metadata.drop_all(db._engine)
    db.Base.metadata.create_all(db._engine)
    yield

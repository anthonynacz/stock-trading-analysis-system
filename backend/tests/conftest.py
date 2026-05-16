"""Pytest config — make `backend/` importable and stub heavy deps."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `backend/` the import root so `from services.x` works.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Provide harmless env defaults so config.Settings doesn't error out at import.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("LEGACY_MODE", "true")
os.environ.setdefault("LEGACY_USER_EMAIL", "test@example.com")

"""Read local credentials without mutating or logging them."""

import os
from pathlib import Path

from dotenv import dotenv_values


def model_key(root: Path):
    values = dotenv_values(root / ".env")
    key = (
        os.getenv("MODEL_API_KEY")
        or values.get("MODEL_API_KEY")
        or os.getenv("META_MODEL_API_KEY")
        or values.get("META_MODEL_API_KEY")
        or ""
    ).strip()
    if not key:
        raise ValueError("Set MODEL_API_KEY in the environment or ignored .env")
    return key

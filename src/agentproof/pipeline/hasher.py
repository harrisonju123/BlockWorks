"""Canonical content hashing for privacy-preserving storage.

Defines a deterministic serialization format so the same logical
content always produces the same hash, regardless of JSON key
ordering or whitespace differences across agent frameworks.
"""

import hashlib
import json
from typing import Any


def hash_content(content: str | list[dict[str, Any]] | Any) -> str:
    """SHA-256 hash of content using canonical serialization."""
    canonical = _canonicalize(content)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonicalize(content: str | list | dict | Any) -> str:
    """Produce a deterministic string from varied input types."""
    if isinstance(content, (dict, list)):
        return json.dumps(content, sort_keys=True, separators=(",", ":"))

    if isinstance(content, str):
        # Fast-path: skip JSON parsing for strings that can't be JSON objects/arrays
        if content and content[0] in ("{", "["):
            try:
                parsed = json.loads(content)
                return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
            except (json.JSONDecodeError, TypeError):
                pass
        return content.strip()

    return str(content).strip()

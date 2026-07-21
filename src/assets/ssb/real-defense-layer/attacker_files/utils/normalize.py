"""
utils/normalize.py — String normalization utilities.
"""
import unicodedata
import re

def normalize_text(s: str) -> str:
    """Normalize a string to NFC form and strip control characters."""
    s = unicodedata.normalize("NFC", s)
    # Remove control characters except newline and tab
    s = "".join(c for c in s if ord(c) >= 32 or c in "\n\t")
    return s.strip()

def slugify(s: str) -> str:
    """Convert a string to a URL-safe slug."""
    s = normalize_text(s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")

def truncate(s: str, n: int) -> str:
    """Truncate a string to n characters, adding an ellipsis if truncated."""
    if len(s) <= n:
        return s
    return s[:max(0, n-1)] + "\u2026"

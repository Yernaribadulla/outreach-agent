from __future__ import annotations

from typing import Any

from ..verticals import get_vertical


def curated_candidates(vertical: str) -> list[dict[str, Any]]:
    """Return the reviewed local dataset for a configured vertical, if one exists."""
    config = get_vertical(vertical)
    if config.key == "dental":
        from .real_candidates import REAL_ASTANA_CANDIDATES
        return list(REAL_ASTANA_CANDIDATES)
    return []

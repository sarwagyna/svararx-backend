"""In-memory letterhead / PDF template cache invalidation per doctor."""
from __future__ import annotations

_cache_version: dict[str, int] = {}


def get_letterhead_version(doctor_id: str) -> int:
    return _cache_version.get(doctor_id, 0)


def invalidate_letterhead_cache(doctor_id: str) -> None:
    _cache_version[doctor_id] = _cache_version.get(doctor_id, 0) + 1


def clear_all_letterhead_cache() -> None:
    _cache_version.clear()

from __future__ import annotations

import diskcache


def open_cache(cache_dir: str):
    return diskcache.Cache(cache_dir)

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_registered = False


def register_pillow_heif() -> bool:
    global _registered
    if _registered:
        return True
    try:
        from pillow_heif import register_heif_opener
    except Exception as exc:
        logger.info("pillow-heif unavailable: %s", exc)
        return False
    register_heif_opener()
    _registered = True
    return True

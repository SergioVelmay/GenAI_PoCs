from __future__ import annotations

import uuid


class NotFoundError(LookupError):
    pass


def new_id(prefix: str) -> str:
    """Identificador opaco con prefijo legible (ord_..., ref_...), estilo API pública."""
    return f"{prefix}_{uuid.uuid4().hex}"

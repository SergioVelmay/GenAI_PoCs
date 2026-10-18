"""Politica determinista de acceso al conocimiento."""

from collections.abc import Mapping
from dataclasses import dataclass

# Politica didactica de clasificacion por tipo de documento. En produccion la decide el
# propietario del dato; aqui es la unica fuente de verdad para local y para la nube.
PUBLIC_DOCUMENT_TYPES = frozenset({"policy", "faq", "venue_guide", "event_sheet"})


def classify_acl(doc_type: str) -> str:
    """`public` para los tipos de cara al cliente; `internal` el resto (cerrado por defecto)."""
    return "public" if doc_type in PUBLIC_DOCUMENT_TYPES else "internal"


@dataclass(frozen=True, slots=True)
class AccessScope:
    allowed_acls: tuple[str, ...] = ("public",)

    def permits(self, metadata: Mapping[str, object]) -> bool:
        return metadata.get("acl") in self.allowed_acls

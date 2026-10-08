"""Politica determinista de acceso al conocimiento."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccessScope:
    allowed_acls: tuple[str, ...] = ("public",)

    def permits(self, metadata: Mapping[str, object]) -> bool:
        return metadata.get("acl") in self.allowed_acls

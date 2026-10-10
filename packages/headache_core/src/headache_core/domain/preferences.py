"""Memoria a largo plazo del cliente: solo preferencias de una lista cerrada y validadas aquí.

El modelo propone qué recordar; el dominio decide si es una preferencia admisible. Así un
prompt injection no puede escribir texto libre en la memoria que se reinyecta en cada prompt
(memory poisoning): como mucho fija una ciudad o una categoría válidas.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum

from headache_core.domain.models import EventCategory

_CITY = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,58}[A-Za-zÀ-ÿ.]$")


class PreferenceKey(StrEnum):
    LANGUAGE = "language"
    HOME_CITY = "home_city"
    FAVORITE_CATEGORY = "favorite_category"


class InvalidPreferenceError(ValueError):
    pass


def normalize_preference(key: PreferenceKey, value: str) -> str:
    value = " ".join(value.split())
    match key:
        case PreferenceKey.LANGUAGE:
            if value.lower() not in ("es", "en"):
                raise InvalidPreferenceError("Idioma no admitido: usa 'es' o 'en'.")
            return value.lower()
        case PreferenceKey.FAVORITE_CATEGORY:
            try:
                return EventCategory(value.lower()).value
            except ValueError:
                options = ", ".join(c.value for c in EventCategory)
                raise InvalidPreferenceError(f"Categoría no válida. Opciones: {options}.") from None
        case PreferenceKey.HOME_CITY:
            if not _CITY.match(value):
                raise InvalidPreferenceError("Ciudad no válida: solo letras, sin instrucciones.")
            return value.title()


def render_preferences(preferences: Mapping[str, str]) -> str:
    # Orden fijo: el mismo contenido produce siempre el mismo texto (bueno para la caché).
    return "\n".join(
        f"- {key}: {preferences[key]}" for key in sorted(preferences) if preferences[key]
    )

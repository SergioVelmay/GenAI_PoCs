"""Reglas de la caché semántica (fase 10.3). Deterministas: el dominio decide qué se reutiliza.

Una caché semántica devuelve la respuesta de una pregunta *parecida*. El riesgo no es fallar
(se llama al modelo y ya), sino el **falso acierto**: "¿a qué hora abren las puertas en
Barcelona?" y "...en Chicago?" están a una palabra de distancia y cualquier embedding las ve
casi iguales. Por eso la similitud solo propone candidatos y el dominio exige además que las
**facetas** coincidan: ciudades, categorías, identificadores, números y la operación (reembolso
frente a cambio de fecha).

Y hay preguntas que no se cachean nunca: las personales (un pedido, una cuenta). La respuesta
depende de quién pregunta, no solo de qué pregunta.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from headache_core.domain.models import EventCategory

type Language = Literal["es", "en", "unknown"]

# Umbral de similitud por modelo de embeddings, medido con `rag_evals.semantic_cache`: el de
# más aciertos con 0 falsos aciertos (con la guarda de facetas). Un modelo sin calibrar no
# tiene umbral: no se activa la caché con él.
CALIBRATED_THRESHOLDS: dict[str, float] = {
    "hashing": 0.55,
}

CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Madrid": ("madrid",),
    "Barcelona": ("barcelona",),
    "London": ("london", "londres"),
    "Paris": ("paris",),
    "Lisbon": ("lisbon", "lisboa"),
    "Milan": ("milan", "milano"),
    "New York": ("new york", "nueva york", "nyc"),
    # "LA" no: en español es un artículo.
    "Los Angeles": ("los angeles",),
    "Chicago": ("chicago",),
    "Mexico City": ("mexico city", "ciudad de mexico", "cdmx"),
}

CATEGORY_KEYWORDS: dict[EventCategory, tuple[str, ...]] = {
    EventCategory.CANDLELIT: ("velas", "candlelit", "candle"),
    EventCategory.EXHIBITION: ("exposicion", "exposiciones", "exhibition", "exhibitions", "museo"),
    EventCategory.THEATRE: ("teatro", "theatre", "theater", "espectaculo", "espectaculos"),
    EventCategory.FOOD_DRINK: (
        "gastronomia",
        "gastronomica",
        "comida",
        "cata",
        "vino",
        "food",
        "drink",
        "wine",
        "tasting",
    ),
    EventCategory.TOURS: ("tour", "tours", "visita guiada", "visitas guiadas", "sightseeing"),
    EventCategory.NIGHTLIFE: ("ocio nocturno", "nightlife", "discoteca", "club nocturno"),
    EventCategory.SPORTS: ("deporte", "deportes", "deportiva", "deportivas", "sport", "sports"),
}

# Operaciones que un embedding confunde (mismo vocabulario de entradas y fechas) y cuya
# respuesta cambia por completo.
OPERATION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "refund": ("reembols", "devolu", "devolver", "devuelv", "refund", "money back"),
    "date_change": (
        "cambiar la fecha",
        "cambio de fecha",
        "cambiar de fecha",
        "otra fecha",
        "change the date",
        "change date",
        "reschedule",
    ),
    "transfer": ("transferir", "ceder", "regalar", "transfer", "give my ticket"),
}

# Marcas de una pregunta personal: su respuesta depende de quién pregunta.
PERSONAL_PATTERNS = (
    r"\bord_\w+",
    r"\bcus_\w+",
    r"[\w.+-]+@[\w-]+\.[\w.]+",
    r"\bmi (pedido|reserva|cuenta|compra|reembolso|tarjeta)\b",
    r"\bmis (pedidos|reservas|compras)\b",
    r"\bmy (order|booking|account|purchase|refund|card)s?\b",
    r"\b\d{9,}\b",
)

_ID_PATTERN = re.compile(r"\b(?:evt|ven|ses)_\w+")
_NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_ES_MARKERS = frozenset(
    [
        "el",
        "la",
        "los",
        "las",
        "de",
        "del",
        "que",
        "en",
        "por",
        "para",
        "con",
        "como",
        "cuando",
        "donde",
        "puedo",
        "hay",
        "mi",
        "una",
        "un",
        "es",
        "se",
        "y",
        "o",
        "al",
        "su",
        "sus",
        "entradas",
        "entrada",
        "cuanto",
        "cuanta",
        "cual",
    ]
)
_EN_MARKERS = frozenset(
    [
        "the",
        "of",
        "to",
        "in",
        "for",
        "with",
        "how",
        "when",
        "where",
        "can",
        "is",
        "are",
        "my",
        "a",
        "an",
        "do",
        "does",
        "i",
        "what",
        "which",
        "tickets",
        "ticket",
        "and",
        "or",
    ]
)


def normalize(text: str) -> str:
    """Minúsculas, sin tildes y con espacios simples: "Lisboa" y "lisboa" son lo mismo."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[\w@.+-]+", stripped))


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}", text) is not None


def detect_language(text: str) -> Language:
    """Heurística de palabras frecuentes. Basta para separar el espacio de la caché por idioma
    (una respuesta en inglés no sirve a quien pregunta en español)."""
    if "¿" in text or "¡" in text:
        return "es"
    words = normalize(text).split()
    spanish = sum(word in _ES_MARKERS for word in words)
    english = sum(word in _EN_MARKERS for word in words)
    if spanish == english:
        return "unknown"
    return "es" if spanish > english else "en"


@dataclass(frozen=True, slots=True)
class QueryFacets:
    """Lo que tiene que coincidir, además de la similitud, para reutilizar una respuesta."""

    cities: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    operations: frozenset[str] = frozenset()
    ids: frozenset[str] = frozenset()
    numbers: frozenset[str] = frozenset()
    personal: bool = False

    def matches(self, other: QueryFacets) -> bool:
        return (
            self.cities == other.cities
            and self.categories == other.categories
            and self.operations == other.operations
            and self.ids == other.ids
            and self.numbers == other.numbers
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "cities": sorted(self.cities),
            "categories": sorted(self.categories),
            "operations": sorted(self.operations),
            "ids": sorted(self.ids),
            "numbers": sorted(self.numbers),
        }

    @classmethod
    def from_dict(cls, data: dict[str, list[str]]) -> QueryFacets:
        return cls(
            cities=frozenset(data.get("cities", ())),
            categories=frozenset(data.get("categories", ())),
            operations=frozenset(data.get("operations", ())),
            ids=frozenset(data.get("ids", ())),
            numbers=frozenset(data.get("numbers", ())),
        )


def extract_facets(question: str) -> QueryFacets:
    text = normalize(question)
    return QueryFacets(
        cities=frozenset(
            city
            for city, aliases in CITY_ALIASES.items()
            if any(_contains(text, alias) for alias in aliases)
        ),
        categories=frozenset(
            str(category)
            for category, words in CATEGORY_KEYWORDS.items()
            if any(_contains(text, word) for word in words)
        ),
        operations=frozenset(
            operation
            for operation, words in OPERATION_KEYWORDS.items()
            if any(_contains(text, word) for word in words)
        ),
        ids=frozenset(_ID_PATTERN.findall(text)),
        # Del texto original: `normalize` parte "17,50" en dos números.
        numbers=frozenset(n.replace(",", ".") for n in _NUMBER_PATTERN.findall(question)),
        personal=any(re.search(pattern, text) for pattern in PERSONAL_PATTERNS),
    )


@dataclass(frozen=True, slots=True)
class CacheScope:
    """Espacio de la caché: solo comparten respuestas quienes verían las mismas fuentes, en el
    mismo idioma, sobre la misma versión del índice y con el mismo modo de respuesta.

    Cambiar `index_version` (reindexar, cambiar una política) invalida todo de golpe sin
    recorrer claves: las entradas viejas quedan en otro espacio y caducan solas.
    """

    acls: tuple[str, ...]
    language: Language
    index_version: str
    mode: str

    def key(self) -> str:
        raw = f"{','.join(sorted(self.acls))}|{self.language}|{self.index_version}|{self.mode}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def is_cacheable_answer(*, abstained: bool, citations: int) -> bool:
    """Solo respuestas con fuentes: una abstención o una respuesta sin citas no se reutiliza."""
    return not abstained and citations > 0

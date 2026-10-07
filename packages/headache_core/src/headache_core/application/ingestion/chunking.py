"""Estrategias de chunking deterministas (Python puro, sin red).

El tamaño se mide en palabras como aproximación barata a tokens (~1,3 tokens por palabra en
inglés/español). El chunking semántico y el jerárquico (parent-child) se hacen en el carril C
con LlamaIndex, porque el primero necesita embeddings y el segundo un docstore.
"""

from __future__ import annotations

import re
from typing import Protocol

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class Chunker(Protocol):
    @property
    def name(self) -> str: ...

    def split(self, text: str) -> list[str]: ...


def _word_count(text: str) -> int:
    return len(text.split())


def _windows(words: list[str], size: int, overlap: int) -> list[str]:
    step = size - overlap
    return [
        " ".join(words[start : start + size])
        for start in range(0, max(len(words) - overlap, 1), step)
        if words[start : start + size]
    ]


class FixedSizeChunker:
    """Ventanas de N palabras con solape. Rápido y predecible, pero corta frases por la mitad."""

    def __init__(self, max_words: int = 80, overlap_words: int = 15) -> None:
        if not 0 <= overlap_words < max_words:
            raise ValueError("El solape debe ser >= 0 y menor que el tamaño del chunk.")
        self._max_words = max_words
        self._overlap_words = overlap_words

    @property
    def name(self) -> str:
        return f"fixed-{self._max_words}-{self._overlap_words}"

    def split(self, text: str) -> list[str]:
        return _windows(text.split(), self._max_words, self._overlap_words)


class SentenceChunker:
    """Agrupa líneas Markdown (títulos, ítems de lista) y frases enteras hasta `max_words`.

    El solape son las últimas unidades completas del chunk anterior (hasta `overlap_words`),
    así nunca se corta una frase. Una frase más larga que el máximo se trocea en ventanas.
    """

    def __init__(self, max_words: int = 80, overlap_words: int = 15) -> None:
        if not 0 <= overlap_words < max_words:
            raise ValueError("El solape debe ser >= 0 y menor que el tamaño del chunk.")
        self._max_words = max_words
        self._overlap_words = overlap_words

    @property
    def name(self) -> str:
        return f"sentence-{self._max_words}-{self._overlap_words}"

    def split(self, text: str) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        carried = 0  # Unidades al inicio de `current` que vienen del solape.
        for unit in self._units(text):
            size = _word_count(unit)
            if current and sum(map(_word_count, current)) + size > self._max_words:
                if len(current) > carried:
                    chunks.append("\n".join(current))
                current = self._overlap(current, budget=self._max_words - size)
                carried = len(current)
            current.append(unit)
        if len(current) > carried:
            chunks.append("\n".join(current))
        return chunks

    def _units(self, text: str) -> list[str]:
        units: list[str] = []
        for line in text.splitlines():
            for sentence in _SENTENCE_SPLIT.split(line.strip()):
                if not sentence:
                    continue
                if _word_count(sentence) > self._max_words:
                    units.extend(_windows(sentence.split(), self._max_words, 0))
                else:
                    units.append(sentence)
        return units

    def _overlap(self, units: list[str], *, budget: int) -> list[str]:
        carry: list[str] = []
        words = 0
        for unit in reversed(units):
            words += _word_count(unit)
            if words > min(self._overlap_words, budget):
                break
            carry.insert(0, unit)
        return carry

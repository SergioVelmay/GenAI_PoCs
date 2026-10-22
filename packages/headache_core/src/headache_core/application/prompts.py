"""Prompts versionados (fase 12.6): Git es la fuente canónica; Langfuse, el escaparate.

Cada paquete guarda sus prompts en `<paquete>/prompts/<nombre>.md` con una cabecera:

    ---
    name: booking-agent
    version: 3
    description: Para qué sirve.
    format: plain            # plain | python (variables con str.format)
    fragment: true           # opcional: solo se incluye en otros ({{> nombre}})
    final_newline: false     # opcional: el texto no acaba en salto de línea
    ---
    Texto del prompt...

- En ejecución el prompt se lee del paquete (sin red): un fallo de Langfuse no tumba un agente.
- `prompts.lock.json` en la raíz guarda versión y hash de cada uno: la CI falla si un texto
  cambia sin subir la versión (`scripts/prompts.py check`).
- `scripts/prompts.py publish --apply` sube cada versión nueva a Langfuse con la MISMA versión,
  y cada llamada al modelo lleva `langfuse.observation.prompt.name/version`: en Langfuse cada
  generación queda enlazada con la versión exacta del prompt que la produjo.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_INCLUDE = re.compile(r"\{\{> ([a-z0-9-]+)\}\}")
_HEADER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

type PromptFormat = Literal["plain", "python"]


class PromptError(ValueError):
    """Fichero de prompt mal formado, inclusión desconocida o variables que no cuadran."""


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: int
    description: str
    text: str
    format: PromptFormat = "plain"
    fragment: bool = False

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def static_prefix(self) -> str:
        """Lo que no cambia al renderizar: todo el texto o, con variables, hasta la primera."""
        return self.text if self.format == "plain" else self.text.split("{", 1)[0]

    def render(self, **values: object) -> str:
        if self.format == "plain":
            if values:
                raise PromptError(f"{self.name} no admite variables.")
            return self.text
        return self.text.format(**values)


def _parse(path: Path) -> tuple[dict[str, str], str]:
    content = path.read_text(encoding="utf-8")
    match = _HEADER.match(content)
    if match is None:
        raise PromptError(f"{path.name}: falta la cabecera --- ... ---")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            raise PromptError(f"{path.name}: línea de cabecera inválida: {line!r}")
        meta[key.strip()] = value.strip()
    body = content[match.end() :]
    if meta.get("final_newline") == "false":
        body = body.removesuffix("\n")
    return meta, body


class PromptCatalog:
    """Los prompts de un paquete, con las inclusiones (`{{> nombre}}`) ya resueltas."""

    def __init__(self, prompts: dict[str, Prompt]) -> None:
        self._prompts = prompts

    @classmethod
    def from_directory(cls, directory: Path) -> PromptCatalog:
        raw: dict[str, tuple[dict[str, str], str]] = {}
        for path in sorted(directory.glob("*.md")):
            meta, body = _parse(path)
            if meta.get("name") != path.stem:
                raise PromptError(f"{path.name}: name debe coincidir con el fichero")
            raw[path.stem] = (meta, body)

        def resolve(name: str, seen: tuple[str, ...]) -> str:
            if name in seen:
                raise PromptError(f"Inclusión circular: {' -> '.join((*seen, name))}")
            if name not in raw:
                raise PromptError(f"Inclusión desconocida: {name}")
            body = raw[name][1]
            return _INCLUDE.sub(lambda m: resolve(m.group(1), (*seen, name)), body)

        prompts: dict[str, Prompt] = {}
        for name, (meta, _) in raw.items():
            fmt = meta.get("format", "plain")
            if fmt not in ("plain", "python"):
                raise PromptError(f"{name}: format {fmt!r} desconocido")
            prompts[name] = Prompt(
                name=name,
                version=int(meta["version"]),
                description=meta.get("description", ""),
                text=resolve(name, ()),
                format=fmt,  # type: ignore[arg-type]
                fragment=meta.get("fragment") == "true",
            )
        for prompt in prompts.values():
            _register(prompt)
        return cls(prompts)

    def get(self, name: str) -> Prompt:
        try:
            return self._prompts[name]
        except KeyError:
            raise PromptError(f"Prompt desconocido: {name}") from None

    def __iter__(self) -> Iterator[Prompt]:
        return iter(self._prompts.values())


# Todos los prompts cargados en el proceso, por nombre: el nombre es único en todo el repo.
_REGISTRY: dict[str, Prompt] = {}


def _register(prompt: Prompt) -> None:
    known = _REGISTRY.get(prompt.name)
    if known is not None and known != prompt:
        raise PromptError(f"Prompt duplicado con otro contenido: {prompt.name}")
    _REGISTRY[prompt.name] = prompt


def registered_prompts() -> list[Prompt]:
    return list(_REGISTRY.values())


_CURRENT_PROMPT: ContextVar[Prompt | None] = ContextVar("current_prompt", default=None)


def current_prompt(system: str | None = None) -> Prompt | None:
    """El prompt de esta llamada al modelo: el del bloque `prompt_scope` o, si no hay, el que
    empieza el mensaje de sistema (los agentes añaden el índice de skills detrás; los prompts
    con variables se reconocen por el texto anterior a la primera)."""
    explicit = _CURRENT_PROMPT.get()
    if explicit is not None or not system:
        return explicit
    candidates = [
        p
        for p in _REGISTRY.values()
        if not p.fragment
        and len(p.static_prefix) >= _MIN_PREFIX
        and system.startswith(p.static_prefix)
    ]
    return max(candidates, key=lambda p: len(p.static_prefix), default=None)


# Con menos texto fijo, dos prompts distintos podrían empezar igual.
_MIN_PREFIX = 40


@contextmanager
def prompt_scope(prompt: Prompt | None) -> Iterator[None]:
    """Las llamadas al modelo dentro del bloque se atribuyen a este prompt (traza y Langfuse)."""
    token = _CURRENT_PROMPT.set(prompt)
    try:
        yield
    finally:
        _CURRENT_PROMPT.reset(token)

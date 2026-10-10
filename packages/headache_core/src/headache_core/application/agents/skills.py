"""Skills: instrucciones y recursos que el agente carga bajo demanda (progressive disclosure).

Nivel 1: el índice (nombre + descripción) va siempre en el prompt de sistema: es estable y barato.
Nivel 2: el cuerpo de SKILL.md llega como resultado de `load_skill` cuando el modelo lo necesita.
Nivel 3: recursos de la carpeta de la skill, también por `load_skill(name, resource)`.

Cargar una skill NO cambia el prompt de sistema ni la lista de tools: el prefijo cacheado se
mantiene y el contenido entra por el final, como un resultado de tool más.

Formato (mismo que Agent Skills / SKILL.md):

    skills/<nombre>/SKILL.md      ---\\nname: <nombre>\\ndescription: <una frase>\\n---\\n<cuerpo>
    skills/<nombre>/<recurso>.md  opcional
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.tools.registry import Tool, ToolContext, ToolError

_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_RESOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.md$")


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    body: str
    resources: dict[str, str] = field(default_factory=dict)


def parse_skill(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, flags=re.DOTALL)
    if match is None:
        raise ValueError("SKILL.md debe empezar con un bloque frontmatter --- ... ---")
    header: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line.strip():
            key, _, value = line.partition(":")
            header[key.strip()] = value.strip()
    return header, match.group(2).strip()


class SkillCatalog:
    def __init__(self, skills: list[Skill]) -> None:
        self._skills = {s.name: s for s in sorted(skills, key=lambda s: s.name)}
        if len(self._skills) != len(skills):
            raise ValueError("Skills con nombre duplicado")

    @classmethod
    def from_directory(cls, root: Path) -> SkillCatalog:
        skills: list[Skill] = []
        for skill_file in sorted(root.glob("*/SKILL.md")):
            header, body = parse_skill(skill_file.read_text(encoding="utf-8"))
            name, description = header.get("name", ""), header.get("description", "")
            if not _SKILL_NAME.match(name) or name != skill_file.parent.name or not description:
                raise ValueError(f"Skill mal definida: {skill_file}")
            # Se leen al arrancar: el modelo solo elige por nombre entre recursos conocidos,
            # nunca una ruta (sin path traversal).
            resources = {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted(skill_file.parent.glob("*.md"))
                if path.name != "SKILL.md" and _RESOURCE_NAME.match(path.name)
            }
            skills.append(Skill(name, description, body, resources))
        return cls(skills)

    @property
    def names(self) -> list[str]:
        return list(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def index(self) -> str:
        if not self._skills:
            return ""
        lines = [f"- {s.name}: {s.description}" for s in self._skills.values()]
        return (
            "Skills disponibles. Antes de una tarea que encaje con una de ellas, cárgala con "
            "load_skill y sigue su procedimiento:\n" + "\n".join(lines)
        )


class LoadSkillArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=64, description="Nombre de la skill del índice.")
    resource: str | None = Field(
        default=None, max_length=68, description="Recurso adicional citado en la skill, o null."
    )


def build_skill_tools(catalog: SkillCatalog) -> list[Tool[LoadSkillArgs]]:
    async def load_skill(args: LoadSkillArgs, _: ToolContext) -> dict[str, str]:
        skill = catalog.get(args.name)
        if skill is None:
            raise ToolError(
                f"No existe la skill '{args.name}'. Disponibles: {', '.join(catalog.names)}.",
                kind="not_found",
            )
        if args.resource is None:
            listed = ", ".join(skill.resources) or "ninguno"
            return {"skill": skill.name, "instructions": skill.body, "resources": listed}
        content = skill.resources.get(args.resource)
        if content is None:
            raise ToolError(
                f"La skill '{skill.name}' no tiene el recurso indicado.", kind="not_found"
            )
        return {"skill": skill.name, "resource": args.resource, "content": content}

    return [
        Tool(
            "load_skill",
            "Carga las instrucciones de una skill del índice (o uno de sus recursos).",
            LoadSkillArgs,
            load_skill,
        )
    ]

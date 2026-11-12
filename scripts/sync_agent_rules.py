"""Genera las reglas de Claude Code (`.claude/rules/`) a partir de las instrucciones de Copilot.

La fuente única de las instrucciones por área es `.github/instructions/<area>.instructions.md`
(frontmatter `applyTo`). Claude Code no lee esa carpeta: carga `.claude/rules/*.md` cuando toca un
fichero que casa con su `paths`. Cada regla generada es un puntero de pocas líneas con los mismos
globs que dice "lee y aplica la instrucción". No se copia el contenido (los `@import` no se expanden
dentro de una regla con `paths`: comprobado el 11-11-2026), así que no hay nada que se desincronice
salvo los globs, y eso lo vigila `--check` en la CI.

    uv run python scripts/sync_agent_rules.py            # regenera .claude/rules/
    uv run python scripts/sync_agent_rules.py --check    # falla si algo no está al día (CI)
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_DIR = Path(".github/instructions")
RULES_DIR = Path(".claude/rules")
SUFFIX = ".instructions.md"
MARKER = "<!-- Generado por scripts/sync_agent_rules.py: no editar a mano. -->"

_FRONTMATTER = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
_FIELD = re.compile(r'^(?P<key>applyTo|description):\s*"(?P<value>.*)"\s*$', re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Instruction:
    area: str
    source: Path
    globs: tuple[str, ...]
    description: str


def parse(path: Path) -> Instruction:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError(f"{path}: sin frontmatter")
    fields = {m["key"]: m["value"] for m in _FIELD.finditer(match["body"])}
    if "applyTo" not in fields:
        raise ValueError(f"{path}: sin applyTo")
    globs = tuple(g.strip() for g in fields["applyTo"].split(",") if g.strip())
    return Instruction(
        area=path.name.removesuffix(SUFFIX),
        source=path,
        globs=globs,
        description=fields.get("description", ""),
    )


def render(instruction: Instruction) -> str:
    paths = "\n".join(f'  - "{glob}"' for glob in instruction.globs)
    source = instruction.source.as_posix()
    description = instruction.description.rstrip(".")
    return (
        f"---\npaths:\n{paths}\n---\n\n{MARKER}\n\n"
        f"Área **{instruction.area}**: {description}.\n\n"
        f"Antes de editar, lee y aplica `{source}`.\n"
        "Es la fuente única de las reglas de esta área, compartida con GitHub Copilot.\n"
    )


def expected_rules(root: Path) -> dict[Path, str]:
    sources = sorted((root / SOURCE_DIR).glob(f"*{SUFFIX}"))
    rules: dict[Path, str] = {}
    for source in sources:
        instruction = parse(source.relative_to(root))
        rules[RULES_DIR / f"{instruction.area}.md"] = render(instruction)
    return rules


def generated_rules(root: Path) -> set[Path]:
    """Reglas existentes creadas por este script (las escritas a mano no se tocan)."""
    found: set[Path] = set()
    for path in (root / RULES_DIR).glob("*.md"):
        if MARKER in path.read_text(encoding="utf-8"):
            found.add(path.relative_to(root))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="no escribe; falla si hay cambios")
    args = parser.parse_args(argv)
    root = Path.cwd()

    expected = expected_rules(root)
    stale = generated_rules(root) - set(expected)
    outdated = [
        rel
        for rel, content in expected.items()
        if not (root / rel).exists() or (root / rel).read_text(encoding="utf-8") != content
    ]

    if args.check:
        for rel in sorted(outdated):
            print(f"desactualizada: {rel.as_posix()}")
        for rel in sorted(stale):
            print(f"sobra: {rel.as_posix()}")
        if outdated or stale:
            print("Ejecuta: uv run python scripts/sync_agent_rules.py")
            return 1
        print(f"{len(expected)} reglas de .claude/rules al día")
        return 0

    (root / RULES_DIR).mkdir(parents=True, exist_ok=True)
    for rel in outdated:
        (root / rel).write_text(expected[rel], encoding="utf-8", newline="\n")
        print(f"escrita: {rel.as_posix()}")
    for rel in stale:
        (root / rel).unlink()
        print(f"borrada: {rel.as_posix()}")
    print(f"{len(expected)} reglas en {RULES_DIR.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

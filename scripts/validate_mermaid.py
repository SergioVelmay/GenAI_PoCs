"""Valida los diagramas Mermaid de la documentación con mermaid-cli fijado en el repo.

    npm ci --prefix tools/mermaid                                  # una vez (y en la CI)
    uv run python scripts/validate_mermaid.py                      # todos los docs
    uv run python scripts/validate_mermaid.py docs/fase-10-escala.md

Por qué no `npx -y @mermaid-js/mermaid-cli`: `npx` descarga y ejecuta un paquete en cada llamada,
y el modo automático de Claude Code no lo deja pasar aunque esté en `permissions.allow` (pasó en
la 12→13, la 9.G5 y la 10.G5). Aquí la versión está fijada con lockfile en `tools/mermaid/`
(`@mermaid-js/mermaid-cli` 12.2.0 y el script de instalación de puppeteer aprobado), se instala
una vez y se ejecuta con `node`. La CI lo ejecuta en cada PR: un diagrama que no renderiza no
llega a `main` aunque una sesión no haya podido validarlo.

Renderiza cada Markdown con diagramas (`mmdc -i <doc>.md`): un error de sintaxis hace fallar el
script con el mensaje de Mermaid. Las salidas van a `.tmp/mermaid/` (ignorado por Git).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "mermaid"
CLI = TOOL / "node_modules" / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"
PUPPETEER = TOOL / "puppeteer.json"
OUT = ROOT / ".tmp" / "mermaid"
FENCE = "```mermaid"


def documents(paths: list[Path]) -> list[Path]:
    if not paths:
        paths = [*sorted((ROOT / "docs").rglob("*.md")), ROOT / "README.md", ROOT / "AGENTS.md"]
    return [p for p in paths if p.is_file() and FENCE in p.read_text(encoding="utf-8")]


def validate(doc: Path, node: str) -> str | None:
    """None si todos los diagramas del documento renderizan; si no, el error de Mermaid."""
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / doc.name
    result = subprocess.run(
        [node, str(CLI), "-q", "-p", str(PUPPETEER), "-i", str(doc), "-o", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode == 0:
        return None
    # El mensaje útil (línea y qué esperaba el parser) va al principio; luego, la pila de JS.
    lines = (result.stderr or result.stdout).strip().splitlines()
    return "\n".join(line for line in lines[:6] if not line.lstrip().startswith("at "))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Markdown (por defecto, todos)")
    args = parser.parse_args()
    node = shutil.which("node")
    if node is None or not CLI.exists():
        print("Falta mermaid-cli: npm ci --prefix tools/mermaid (necesita Node.js).")
        sys.exit(2)
    docs = documents([p.resolve() for p in args.paths])
    failures = 0
    for doc in docs:
        error = validate(doc, node)
        name = doc.relative_to(ROOT).as_posix()
        if error is None:
            print(f"OK    {name} ({doc.read_text(encoding='utf-8').count(FENCE)} diagramas)")
        else:
            failures += 1
            print(f"FALLO {name}\n{error}")
    print(f"{len(docs) - failures}/{len(docs)} documentos con diagramas válidos.")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()

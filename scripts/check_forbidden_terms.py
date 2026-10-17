"""Bloquea commits que contengan términos prohibidos (nombre real del cliente, marcas de terceros).

Los términos viven en `.secrets/forbidden_terms.txt` (ignorado por Git, uno por línea, `#` para
comentarios): así la lista nunca entra en el repo. Comparación sin mayúsculas ni tildes y por
palabra completa (letras y dígitos delimitan; `_`, `-` y `.` separan): `x` detecta `x_core` y
`x.com`, pero no `xyz`. Para variantes pegadas, añadirlas como término propio.

    uv run python scripts/check_forbidden_terms.py            # lo preparado para el commit
    uv run python scripts/check_forbidden_terms.py --all      # todos los ficheros versionados

Lo ejecuta `.githooks/pre-commit` (`git config core.hooksPath .githooks`). Sin fichero de
términos (p. ej. en la CI) avisa y no bloquea. Nunca imprime el término, solo fichero y línea.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

TERMS_FILE = Path(".secrets/forbidden_terms.txt")


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def load_terms(path: Path) -> list[str]:
    # utf-8-sig: PowerShell 5.1 guarda UTF-8 con BOM.
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [normalize(t.strip()) for t in lines if t.strip() and not t.lstrip().startswith("#")]


def compile_terms(terms: Sequence[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")


def scan(path: str, content: str, pattern: re.Pattern[str]) -> list[Finding]:
    findings = [Finding(path, 0)] if pattern.search(normalize(path)) else []
    for number, line in enumerate(content.splitlines(), start=1):
        if pattern.search(normalize(line)):
            findings.append(Finding(path, number))
    return findings


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _files(all_files: bool) -> list[str]:
    if all_files:
        return _git("ls-files", "-z").split("\0")
    return _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0")


def _content(path: str, *, staged: bool) -> str | None:
    """Contenido tal y como entrará en el commit (índice), no el del disco."""
    try:
        if staged:
            raw = subprocess.run(
                ["git", "show", f":{path}"], check=True, capture_output=True
            ).stdout
        else:
            raw = Path(path).read_bytes()
    except (subprocess.CalledProcessError, OSError):
        return None
    if b"\0" in raw[:8000]:
        return None  # Binario.
    return raw.decode("utf-8", errors="replace")


def check(paths: Iterable[str], pattern: re.Pattern[str], *, staged: bool) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path:
            continue
        content = _content(path, staged=staged)
        findings.extend(scan(path, content or "", pattern))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Revisa todos los ficheros versionados")
    parser.add_argument("--terms", type=Path, default=TERMS_FILE)
    args = parser.parse_args(argv)

    if not args.terms.exists():
        print(f"[forbidden-terms] Sin {args.terms}: no se comprueba nada.", file=sys.stderr)
        return 0
    terms = load_terms(args.terms)
    if not terms:
        print(f"[forbidden-terms] {args.terms} está vacío.", file=sys.stderr)
        return 0

    findings = check(_files(args.all), compile_terms(terms), staged=not args.all)
    if not findings:
        return 0
    print("[forbidden-terms] Términos prohibidos encontrados (no se muestran):", file=sys.stderr)
    for f in findings:
        where = "ruta del fichero" if f.line == 0 else f"línea {f.line}"
        print(f"  {f.path}: {where}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

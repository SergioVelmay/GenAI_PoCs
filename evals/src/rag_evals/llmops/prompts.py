"""Prompts versionados (fase 12.6): candado en Git y publicación en Langfuse.

    uv run python -m rag_evals.llmops.prompts check           # la CI: falla si un texto cambia
                                                             # sin subir su versión
    uv run python -m rag_evals.llmops.prompts lock            # tras subir versiones a propósito
    uv run python -m rag_evals.llmops.prompts publish [--apply]   # dry-run por defecto

Reglas del candado (`prompts.lock.json`, revisado en cada PR como cualquier código):
- Texto distinto con la misma versión: error (el cambio silencioso es justo lo que se evita).
  `lock` se niega a absorberlo.
- Versión nueva sin el candado actualizado, prompt nuevo o borrado: error hasta ejecutar `lock`.
- Versión que baja o que sube sin cambiar el texto: error.

Publicación: la versión de Git ES la de Langfuse. Si la última publicada es la anterior, se crea
la nueva (etiquetas `production` y `git-vN`); si ya está y el texto coincide, nada; si no
cuadra (texto distinto o versiones desalineadas), se para: nunca se reescribe una versión.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from headache_core.application.prompts import Prompt
from headache_core.ports.prompts import PromptStorePort
from rag_evals import EVALS_DIR

ROOT = EVALS_DIR.parent
LOCK_PATH = ROOT / "prompts.lock.json"
# Un catálogo por paquete (el dueño del prompt es el dueño del código).
CATALOGS = (
    "headache_core.prompts",
    "harness.prompts",
    "booking_agent.prompts",
    "orchestrator.prompts",
    "headache_mcp.prompts",
    "rag_evals.prompts",
)
# Problemas que `lock` resuelve; el resto (cambio silencioso, versión que baja) no.
LOCKABLE = "(ejecuta lock)"


@dataclass(frozen=True, slots=True)
class Entry:
    package: str
    prompt: Prompt


def discover(catalogs: Sequence[str] = CATALOGS) -> list[Entry]:
    entries: list[Entry] = []
    seen: dict[str, str] = {}
    for module in catalogs:
        for prompt in importlib.import_module(module).PROMPTS:
            if prompt.name in seen:
                raise SystemExit(f"Prompt {prompt.name} en {seen[prompt.name]} y en {module}")
            seen[prompt.name] = module
            entries.append(Entry(module.removesuffix(".prompts"), prompt))
    return sorted(entries, key=lambda e: e.prompt.name)


def prompt_dirs() -> set[Path]:
    """Todas las carpetas `prompts/` del repo: ninguna puede quedar fuera de `CATALOGS`."""
    patterns = ("packages/*/src/*/prompts", "services/*/src/*/prompts", "evals/src/*/prompts")
    return {path.resolve() for pattern in patterns for path in ROOT.glob(pattern)}


def catalog_dirs(catalogs: Sequence[str] = CATALOGS) -> set[Path]:
    return {Path(importlib.import_module(m).__file__ or "").parent.resolve() for m in catalogs}


def lock_data(entries: Iterable[Entry]) -> dict[str, dict[str, object]]:
    return {
        e.prompt.name: {
            "package": e.package,
            "version": e.prompt.version,
            "sha256": e.prompt.sha256,
            "fragment": e.prompt.fragment,
        }
        for e in entries
    }


def check(entries: Sequence[Entry], lock: Mapping[str, Mapping[str, object]]) -> list[str]:
    problems: list[str] = []
    current = lock_data(entries)
    for name in sorted(set(lock) - set(current)):
        problems.append(f"{name}: está en el candado pero ya no existe {LOCKABLE}")
    for name, now in current.items():
        locked = lock.get(name)
        if locked is None:
            problems.append(f"{name}: prompt nuevo sin candado {LOCKABLE}")
            continue
        version, old_version = int(str(now["version"])), int(str(locked["version"]))
        changed = now["sha256"] != locked["sha256"]
        if version < old_version:
            problems.append(f"{name}: la versión baja de {old_version} a {version}")
        elif version == old_version and changed:
            problems.append(f"{name}: el texto cambia sin subir la versión ({version})")
        elif version > old_version and not changed:
            problems.append(f"{name}: sube a {version} sin cambiar el texto")
        elif version > old_version:
            problems.append(f"{name}: versión {version} sin candado {LOCKABLE}")
        elif now["package"] != locked["package"]:
            problems.append(f"{name}: cambia de paquete {LOCKABLE}")
    return problems


def load_lock(path: Path = LOCK_PATH) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    data: dict[str, dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))["prompts"]
    return data


def lock(entries: Sequence[Entry], path: Path = LOCK_PATH) -> list[str]:
    """Escribe el candado si solo faltaba eso; si no, devuelve lo que lo impide."""
    blocking = [p for p in check(entries, load_lock(path)) if not p.endswith(LOCKABLE)]
    if not blocking:
        payload = {"prompts": lock_data(entries)}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return blocking


@dataclass(frozen=True, slots=True)
class PublishStep:
    name: str
    version: int
    action: str  # create | up_to_date | conflict
    detail: str = ""


def plan_publish(entries: Sequence[Entry], store: PromptStorePort) -> list[PublishStep]:
    steps: list[PublishStep] = []
    for entry in entries:
        prompt = entry.prompt
        if prompt.fragment:
            continue  # Se publica resuelto dentro de quien lo incluye.
        latest = store.latest(prompt.name)
        published = 0 if latest is None else latest.version
        if latest is not None and published == prompt.version:
            same = latest.text == prompt.text
            action, detail = ("up_to_date", "") if same else ("conflict", "otro texto publicado")
            steps.append(PublishStep(prompt.name, prompt.version, action, detail))
        elif published == prompt.version - 1:
            steps.append(PublishStep(prompt.name, prompt.version, "create"))
        else:
            detail = f"Langfuse va por la {published}; Git, por la {prompt.version}"
            steps.append(PublishStep(prompt.name, prompt.version, "conflict", detail))
    return steps


def apply_publish(
    entries: Sequence[Entry], steps: Sequence[PublishStep], store: PromptStorePort, commit: str
) -> int:
    by_name = {e.prompt.name: e for e in entries}
    created_count = 0
    for step in steps:
        if step.action != "create":
            continue
        entry = by_name[step.name]
        prompt = entry.prompt
        created = store.create(
            prompt.name,
            prompt.text,
            labels=["production", f"git-v{prompt.version}"],
            config={
                "git_version": prompt.version,
                "git_commit": commit,
                "sha256": prompt.sha256,
                "format": prompt.format,
                "package": entry.package,
            },
            commit_message=f"{prompt.name} v{prompt.version} ({commit[:7]}): {prompt.description}",
        )
        if created != prompt.version:
            raise SystemExit(
                f"{prompt.name}: Langfuse creó la {created}; Git dice {prompt.version}"
            )
        created_count += 1
    return created_count


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=ROOT
    )
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Falla si el candado no cuadra (lo ejecuta la CI)")
    commands.add_parser("lock", help="Actualiza el candado tras subir versiones")
    publish = commands.add_parser("publish", help="Publica versiones nuevas en Langfuse")
    publish.add_argument("--apply", action="store_true", help="Sin esto, solo muestra el plan")
    args = parser.parse_args()

    if missing := prompt_dirs() - catalog_dirs():
        raise SystemExit(f"Carpetas de prompts fuera de CATALOGS: {sorted(map(str, missing))}")
    entries = discover()
    if args.command == "check":
        problems = check(entries, load_lock())
        for problem in problems:
            print(f"  FALLO {problem}")
        print(f"{len(entries)} prompts, {len(problems)} problemas")
        sys.exit(1 if problems else 0)
    if args.command == "lock":
        if blocking := lock(entries):
            for problem in blocking:
                print(f"  FALLO {problem}")
            raise SystemExit("El candado no absorbe cambios sin versión: sube la versión.")
        print(f"Candado actualizado: {len(entries)} prompts en {LOCK_PATH.name}")
        return
    if check(entries, load_lock()):
        raise SystemExit("Antes de publicar, el candado debe cuadrar (check).")
    from headache_core.adapters.langfuse_prompts import LangfusePromptStore
    from headache_core.config import Settings

    store = LangfusePromptStore.from_settings(Settings().langfuse)
    steps = plan_publish(entries, store)
    for step in steps:
        print(f"  {step.action:>10}  {step.name} v{step.version}  {step.detail}")
    if any(step.action == "conflict" for step in steps):
        raise SystemExit("Hay conflictos: no se publica nada.")
    if args.apply:
        print(f"Publicadas: {apply_publish(entries, steps, store, _git_commit())}")
    else:
        print("Dry-run: añade --apply para publicar.")


if __name__ == "__main__":
    main()

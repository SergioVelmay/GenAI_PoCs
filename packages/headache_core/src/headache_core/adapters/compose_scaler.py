"""Actuador local del autoescalado: `docker compose --scale` (fase 10.6).

Equivale a lo que hace el HPA sobre un Deployment (AKS), el escalado de una Container App
(ACA, KEDA gestionado) o `desired_count` de un servicio ECS: cambiar el número de copias
idénticas de un worker. `--no-recreate` evita reiniciar las réplicas que ya estaban.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence


class ComposeScalerError(Exception):
    pass


class DockerComposeScaler:
    def __init__(
        self, *, profiles: Sequence[str] = ("scale",), project_dir: str | None = None
    ) -> None:
        self._base = ["docker", "compose"]
        for profile in profiles:
            self._base += ["--profile", profile]
        self._project_dir = project_dir

    def ps_command(self, service: str) -> list[str]:
        # Las réplicas que existen (también las que se reinician), no solo las sanas: como
        # `spec.replicas` del HPA. Si contara solo las sanas, un worker que no arranca haría
        # subir réplicas sin fin (visto en la demo con una imagen rota).
        return [*self._base, "ps", "--format", "json", service]

    def scale_command(self, service: str, replicas: int) -> list[str]:
        return [
            *self._base,
            "up",
            "-d",
            "--no-recreate",
            "--scale",
            f"{service}={replicas}",
            service,
        ]

    async def replicas(self, service: str) -> int:
        output = await self._run(self.ps_command(service))
        # Según la versión, `ps --format json` da una lista o un objeto JSON por línea.
        text = output.strip()
        if not text:
            return 0
        if text.startswith("["):
            return len(json.loads(text))
        return sum(1 for line in text.splitlines() if line.strip())

    async def scale(self, service: str, replicas: int) -> None:
        await self._run(self.scale_command(service, replicas))

    async def _run(self, command: list[str]) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self._project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise ComposeScalerError(stderr.decode(errors="replace").strip()[:500])
        return stdout.decode(errors="replace")

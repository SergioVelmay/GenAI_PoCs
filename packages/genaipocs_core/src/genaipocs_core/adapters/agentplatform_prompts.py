"""Prompts publicados en la gestión de prompts de Agent Platform (12.G4), como Langfuse (12.6).

El equivalente de los prompts de Foundry y de Bedrock Prompt Management. Git sigue siendo la
fuente: aquí llega una copia para que un equipo que trabaje en la consola de Agent Platform vea
las mismas versiones.

Cómo encaja con `PromptStorePort` (SDK `agentplatform` 2.7, `client.prompts`):

- Un prompt es un **Vertex Dataset** y cada versión, una versión del dataset. El `prompt_id` es el
  número del dataset: el nombre (`prompt_display_name`) se busca con un filtro en `list`.
- `create_version` crea un prompt **nuevo** con su primera versión; `update(prompt_id)` le añade
  otra. El número de versión que devuelve el adaptador es cuántas tiene: así `apply_publish`
  comprueba que la del almacén coincide con la de Git, igual que con Langfuse.
- Sin etiquetas ni metadatos libres: la versión de Git y el commit van en el nombre de la versión
  (`git-v3-1a2b3c4`); el hash y el paquete, en el candado de Git.
- Un prompt exige un modelo y un único turno: el texto va como turno de usuario y el modelo es el
  de chat de Gemini (solo referencia; en ejecución los prompts se leen del paquete).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Protocol, Self

from genaipocs_core.config import Settings
from genaipocs_core.ports.prompts import PublishedPrompt


class PromptsClient(Protocol):
    """Lo que se usa de `agentplatform.Client().prompts` (el doble de los tests lo imita)."""

    def list(self, *, config: Any = None) -> Iterator[Any]: ...

    def list_versions(self, *, prompt_id: str, config: Any = None) -> Iterator[Any]: ...

    def get_version(self, *, prompt_id: str, version_id: str, config: Any = None) -> Any: ...

    def create_version(self, *, prompt: Any, config: Any = None) -> Any: ...

    def update(self, *, prompt_id: str, prompt: Any, config: Any = None) -> Any: ...


def version_name(git_version: object, commit: object) -> str:
    return f"git-v{git_version}-{str(commit)[:7]}"


class AgentPlatformPromptStore:
    def __init__(self, client: PromptsClient, *, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        import agentplatform

        from genaipocs_core.adapters.gcp_auth import gcp_credentials

        gcp = settings.gcp
        if not gcp.project:
            raise ValueError("Falta GCP__PROJECT")
        client = agentplatform.Client(
            project=gcp.project, location=gcp.region, credentials=gcp_credentials(gcp)
        )
        return cls(client.prompts, model=settings.gemini.chat_model)

    def _prompt_id(self, name: str) -> str | None:
        from agentplatform import types

        # Filtro de la API de datasets por nombre visible (comillas dobles, sin comodines).
        refs = list(
            self._client.list(config=types.ListPromptsConfig(filter=f'display_name="{name}"'))
        )
        if len(refs) > 1:
            raise ValueError(f"{len(refs)} prompts se llaman {name}: no se sabe cuál es")
        return str(refs[0].prompt_id) if refs else None

    def _versions(self, prompt_id: str) -> list[str]:
        ids = [str(ref.version_id) for ref in self._client.list_versions(prompt_id=prompt_id)]
        return sorted(ids, key=int)

    def latest(self, name: str) -> PublishedPrompt | None:
        prompt_id = self._prompt_id(name)
        if prompt_id is None:
            return None
        versions = self._versions(prompt_id)
        if not versions:
            return None
        prompt = self._client.get_version(prompt_id=prompt_id, version_id=versions[-1])
        contents = prompt.prompt_data.contents
        text = "".join(part.text or "" for part in contents[0].parts) if contents else ""
        return PublishedPrompt(name, len(versions), text)

    def create(
        self,
        name: str,
        text: str,
        *,
        labels: Sequence[str],
        config: Mapping[str, object],
        commit_message: str,
    ) -> int:
        from agentplatform import types
        from google.genai import types as genai_types

        prompt = types.Prompt(
            prompt_data=types.PromptData(
                model=self._model,
                contents=[genai_types.Content(role="user", parts=[genai_types.Part(text=text)])],
            )
        )
        version = version_name(config.get("git_version", "?"), config.get("git_commit", ""))
        prompt_id = self._prompt_id(name)
        if prompt_id is None:
            self._client.create_version(
                prompt=prompt,
                config=types.CreatePromptVersionConfig(
                    prompt_display_name=name, version_display_name=version
                ),
            )
            prompt_id = self._prompt_id(name)
            if prompt_id is None:
                raise ValueError(f"{name}: creado, pero no aparece al buscarlo por nombre")
        else:
            self._client.update(
                prompt_id=prompt_id,
                prompt=prompt,
                config=types.UpdatePromptConfig(version_display_name=version),
            )
        return len(self._versions(prompt_id))

"""Subida incremental del carril A de Azure contra Azurite (checkpoint G→13.4), sin coste.

    docker compose --profile azure up -d azurite
    uv run pytest -m integration packages/genaipocs_core/tests/integration/test_azurite.py

`needs_upload` depende de que Azure Storage calcule y guarde el Content-MD5 al subir un blob de una
vez (validado en Azure en el checkpoint 8→9). Aquí se prueba el mismo contrato en local: la segunda
pasada no sube nada y solo se resube lo que cambia. Azurite no comprueba tokens ni RBAC: la
identidad gestionada sigue siendo cosa de la nube.
"""

from __future__ import annotations

import dataclasses
import socket
import uuid
from collections.abc import AsyncIterator

import pytest
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from genaipocs_core.adapters.azure_search_managed import (
    blob_metadata,
    blob_name,
    content_md5,
    upload_if_changed,
)
from genaipocs_core.domain import DocumentType, KnowledgeDocument

pytestmark = pytest.mark.integration

# El SDK la traduce a la cuenta de desarrollo de Azurite (devstoreaccount1 en 127.0.0.1:10000,
# con su clave pública y fija): sin claves en el repo.
AZURITE_CONNECTION_STRING = "UseDevelopmentStorage=true"


@pytest.fixture
async def container() -> AsyncIterator[ContainerClient]:
    try:
        socket.create_connection(("127.0.0.1", 10000), timeout=0.5).close()
    except OSError:
        pytest.skip("Azurite no está arrancado en 127.0.0.1:10000")
    async with BlobServiceClient.from_connection_string(AZURITE_CONNECTION_STRING) as service:
        client = await service.create_container(f"it-{uuid.uuid4().hex[:12]}")
        yield client
        await client.delete_container()


def _doc(city: str, content: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=f"faq-{city}-es",
        doc_type=DocumentType.FAQ,
        language="es",
        title=f"Preguntas frecuentes de {city}",
        content=content,
        metadata={"city": city, "category": "candlelit"},
    )


async def test_upload_if_changed_only_uploads_new_or_changed_documents(
    container: ContainerClient,
) -> None:
    madrid = _doc("madrid", "Las puertas abren 30 minutos antes. Señalización en español.")
    sevilla = _doc("sevilla", "Acceso por la puerta norte.")

    first = [await upload_if_changed(container, d) for d in (madrid, sevilla)]
    again = [await upload_if_changed(container, d) for d in (madrid, sevilla)]
    edited = dataclasses.replace(sevilla, content="Acceso por la puerta sur.")
    retagged = dataclasses.replace(madrid, language="en")
    changed = [await upload_if_changed(container, d) for d in (retagged, edited)]

    assert first == [True, True]
    assert again == [False, False]  # Sin cambios: no se toca la fecha de modificación.
    assert changed == [True, True]  # Cambia un metadato en uno y el contenido en otro.

    blob = container.get_blob_client(blob_name(edited))
    properties = await blob.get_blob_properties()
    assert bytes(properties.content_settings.content_md5 or b"") == content_md5(edited)
    assert properties.metadata == blob_metadata(edited)
    downloaded = await (await blob.download_blob()).readall()
    assert downloaded.decode() == "Acceso por la puerta sur."

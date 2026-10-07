"""Carril A en Azure: vectorización integrada de AI Search (indexer + skillset).

Todo lo hace el servicio: el indexer lee los blobs, el skillset trocea (SplitSkill) y llama
al modelo de embeddings (AzureOpenAIEmbeddingSkill), y las *index projections* convierten
cada documento en N chunks del índice. La consulta también se vectoriza en el servicio.
Ingesta incremental (fecha de modificación del blob) y borrados (soft delete) incluidos.

Lo que el código solo DEFINE (y se puede revisar en dry-run sin coste):
data source -> índice de chunks (con vectorizador) -> skillset -> indexer.

Autenticación saliente del servicio de AI Search:
- Tier Basic o superior: identidad gestionada (`ResourceId=...`), sin secretos.
- Tier Free: no admite identidad gestionada -> connection string con clave y API key.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from azure.search.documents.indexes.models import (
    AzureOpenAIEmbeddingSkill,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    HnswParameters,
    IndexingParameters,
    IndexingParametersConfiguration,
    IndexProjectionMode,
    InputFieldMappingEntry,
    NativeBlobSoftDeleteDeletionDetectionPolicy,
    OutputFieldMappingEntry,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataSourceConnection,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionSelector,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerSkillset,
    SimpleField,
    SplitSkill,
    VectorSearch,
    VectorSearchProfile,
)

from headache_core.domain import KnowledgeDocument

# Metadatos del blob que el indexer expone como /document/<clave>. Deben ser ASCII.
BLOB_METADATA = ("document_id", "doc_type", "language", "category", "city")


@dataclass(frozen=True)
class ManagedLaneNames:
    prefix: str = "knowledge-managed"

    @property
    def data_source(self) -> str:
        return f"{self.prefix}-ds"

    @property
    def index(self) -> str:
        return self.prefix

    @property
    def skillset(self) -> str:
        return f"{self.prefix}-skillset"

    @property
    def indexer(self) -> str:
        return f"{self.prefix}-indexer"


@dataclass(frozen=True)
class EmbeddingTarget:
    resource_url: str  # https://<recurso>.openai.azure.com
    deployment: str = "text-embedding-3-small"
    dimensions: int = 1024
    api_key: str | None = None  # None => identidad gestionada del servicio de búsqueda


def blob_name(document: KnowledgeDocument) -> str:
    return f"{document.doc_type.value}/{document.id}.md"


def blob_metadata(document: KnowledgeDocument) -> dict[str, str]:
    """Los metadatos de blob solo admiten ASCII: lo que no lo sea se omite."""
    values = {
        "document_id": document.id,
        "doc_type": document.doc_type.value,
        "language": document.language,
        **document.metadata,
    }
    return {k: v for k, v in values.items() if k in BLOB_METADATA and v.isascii()}


def data_source(
    names: ManagedLaneNames, *, credentials: str, container: str
) -> SearchIndexerDataSourceConnection:
    """`credentials`: `ResourceId=<id de la cuenta>;` (identidad) o connection string con clave."""
    return SearchIndexerDataSourceConnection(
        name=names.data_source,
        type="azureblob",
        connection_string=credentials,
        container=SearchIndexerDataContainer(name=container),
        # Requiere soft delete activado en la cuenta (Terraform): borrar el blob borra sus chunks.
        data_deletion_detection_policy=NativeBlobSoftDeleteDeletionDetectionPolicy(),
    )


def chunk_index(names: ManagedLaneNames, target: EmbeddingTarget) -> SearchIndex:
    vectorizer = "aoai-vectorizer"
    fields = [
        # Las index projections exigen clave con analizador `keyword`.
        SearchableField(
            name="chunk_id", type=SearchFieldDataType.STRING, key=True, analyzer_name="keyword"
        ),
        SimpleField(name="parent_id", type=SearchFieldDataType.STRING, filterable=True),
        SearchableField(name="text", type=SearchFieldDataType.STRING),
        SearchField(
            name="embedding",
            type="Collection(Edm.Single)",
            searchable=True,
            vector_search_dimensions=target.dimensions,
            vector_search_profile_name="hnsw-aoai",
        ),
        *(
            SimpleField(name=field, type=SearchFieldDataType.STRING, filterable=True)
            for field in BLOB_METADATA
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(name="hnsw", parameters=HnswParameters(metric="cosine"))
        ],
        # El vectorizador permite consultar con texto: el servicio calcula el embedding.
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name=vectorizer,
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=target.resource_url,
                    deployment_name=target.deployment,
                    model_name=target.deployment,
                    api_key=target.api_key,
                ),
            )
        ],
        profiles=[
            VectorSearchProfile(
                name="hnsw-aoai", algorithm_configuration_name="hnsw", vectorizer_name=vectorizer
            )
        ],
    )
    return SearchIndex(name=names.index, fields=fields, vector_search=vector_search)


def skillset(
    names: ManagedLaneNames,
    target: EmbeddingTarget,
    *,
    page_length: int = 2000,
    page_overlap: int = 200,
) -> SearchIndexerSkillset:
    """Troceo por páginas de N caracteres (no tokens) con solape, y embedding por página."""
    split = SplitSkill(
        name="split",
        context="/document",
        text_split_mode="pages",
        maximum_page_length=page_length,
        page_overlap_length=page_overlap,
        inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
        outputs=[OutputFieldMappingEntry(name="textItems", target_name="pages")],
    )
    embed = AzureOpenAIEmbeddingSkill(
        name="embed",
        context="/document/pages/*",
        resource_url=target.resource_url,
        deployment_name=target.deployment,
        model_name=target.deployment,
        dimensions=target.dimensions,
        api_key=target.api_key,
        inputs=[InputFieldMappingEntry(name="text", source="/document/pages/*")],
        outputs=[OutputFieldMappingEntry(name="embedding", target_name="vector")],
    )
    projection = SearchIndexerIndexProjection(
        selectors=[
            SearchIndexerIndexProjectionSelector(
                target_index_name=names.index,
                parent_key_field_name="parent_id",
                source_context="/document/pages/*",
                mappings=[
                    InputFieldMappingEntry(name="text", source="/document/pages/*"),
                    InputFieldMappingEntry(name="embedding", source="/document/pages/*/vector"),
                    *(
                        InputFieldMappingEntry(name=field, source=f"/document/{field}")
                        for field in BLOB_METADATA
                    ),
                ],
            )
        ],
        # Solo se indexan los chunks, no el documento padre completo.
        parameters=SearchIndexerIndexProjectionsParameters(
            projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
        ),
    )
    return SearchIndexerSkillset(
        name=names.skillset,
        description="Headache: troceo + embeddings (vectorización integrada)",
        skills=[split, embed],
        index_projection=projection,
    )


def indexer(names: ManagedLaneNames) -> SearchIndexer:
    return SearchIndexer(
        name=names.indexer,
        data_source_name=names.data_source,
        skillset_name=names.skillset,
        target_index_name=names.index,
        parameters=IndexingParameters(
            # Un documento roto no detiene la ingesta del resto (queda en el estado del indexer).
            max_failed_items=-1,
            configuration=IndexingParametersConfiguration(
                parsing_mode="markdown",
                markdown_parsing_submode="oneToOne",
                data_to_extract="contentAndMetadata",
            ),
        ),
    )


def definitions(
    names: ManagedLaneNames, target: EmbeddingTarget, *, credentials: str, container: str
) -> Iterable[object]:
    """En orden de creación: el skillset referencia el índice y el indexer, todo lo anterior."""
    yield data_source(names, credentials=credentials, container=container)
    yield chunk_index(names, target)
    yield skillset(names, target)
    yield indexer(names)

"""Handler de la cola: traduce un mensaje en una llamada al caso de uso de indexación."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from headache_core.application.ingestion import IndexingResult, KnowledgeIndexer
from headache_core.ports import Message, PermanentMessageError
from indexer.messages import IndexingCommand

logger = logging.getLogger(__name__)


class IndexingHandler:
    def __init__(self, indexer: KnowledgeIndexer) -> None:
        self._indexer = indexer

    async def __call__(self, message: Message) -> None:
        try:
            command = IndexingCommand.model_validate(message.payload)
        except ValidationError as exc:
            raise PermanentMessageError(str(exc)) from exc

        result: IndexingResult
        if command.document is None:
            result = await self._indexer.remove(command.document_id)
        else:
            result = await self._indexer.index(command.document.to_domain())
        logger.info(
            "%s %s (+%d/-%d chunks, entrega %d)",
            result.outcome,
            result.document_id,
            result.chunks_upserted,
            result.chunks_deleted,
            message.delivery_count,
        )

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping

import pytest


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg 3 asíncrono (checkpointer de LangGraph) no funciona con el bucle Proactor de Windows.
    if sys.platform == "win32" and item.get_closest_marker("integration"):
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}

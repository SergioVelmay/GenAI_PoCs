"""Fase 6.G3: bundles de Apigee generados desde los contratos MCP (sin nube)."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from genaipocs_core.config import McpSettings
from genaipocs_mcp.apigee import (
    DENY,
    PARSE,
    SPIKE,
    VERIFY,
    build_all,
    bundle_files,
    contract_tools,
    proxy_name,
    write_bundle,
)
from genaipocs_mcp.auth import SERVER_SCOPES

SIGNER = "genaipocs-dev-mcp-gw@genaipocs-dev.iam.gserviceaccount.com"


@pytest.fixture
def bundles(tmp_path: Path) -> dict[str, dict[str, str]]:
    paths = build_all(tmp_path, signer=SIGNER, settings=McpSettings())
    result = {}
    for path in paths:
        with zipfile.ZipFile(path) as bundle:
            result[path.stem] = {n: bundle.read(n).decode("utf-8") for n in bundle.namelist()}
    return result


def test_one_well_formed_proxy_per_server(bundles: dict[str, dict[str, str]]) -> None:
    assert set(bundles) == {proxy_name(s) for s in SERVER_SCOPES}
    for name, files in bundles.items():
        assert f"apiproxy/{name}.xml" in files
        for xml in files.values():
            ElementTree.fromstring(xml)  # Apigee rechaza un bundle con XML mal formado.
        policies = {n.rsplit("/", 1)[-1] for n in files if "/policies/" in n}
        assert policies == {f"{p}.xml" for p in (PARSE, VERIFY, DENY, SPIKE)}


def test_each_proxy_verifies_the_token_of_its_own_server(
    bundles: dict[str, dict[str, str]],
) -> None:
    verify = ElementTree.fromstring(bundles["genaipocs-refunds"][f"apiproxy/policies/{VERIFY}.xml"])

    assert verify.findtext("Issuer") == SIGNER
    assert verify.findtext("Audience") == "api://genaipocs-mcp/refunds"
    assert verify.findtext("AdditionalClaims/Claim[@name='scope']") == "refunds:write"
    jwks = verify.find("PublicKey/JWKS")
    assert jwks is not None
    assert jwks.get("uri", "").endswith(f"/metadata/jwk/{SIGNER}")


def test_only_the_tools_of_the_contract_get_through(bundles: dict[str, dict[str, str]]) -> None:
    proxy = ElementTree.fromstring(bundles["genaipocs-ticketing"]["apiproxy/proxies/default.xml"])
    steps = {s.findtext("Name"): s.findtext("Condition") for s in proxy.iter("Step")}

    # Orden: parsear, autenticar, filtrar tools y limitar por cliente.
    assert list(steps) == [PARSE, VERIFY, DENY, SPIKE]
    condition = steps[DENY] or ""
    allowed = re.search(r"\^\((.+)\)\$", condition)
    assert allowed is not None
    assert sorted(allowed.group(1).split("|")) == sorted(contract_tools("ticketing"))
    assert "request_refund" not in condition  # La escritura solo pasa por el proxy de refunds.


def test_the_target_is_a_target_server_not_a_hardcoded_url(
    bundles: dict[str, dict[str, str]],
) -> None:
    target = ElementTree.fromstring(bundles["genaipocs-catalog"]["apiproxy/targets/default.xml"])

    server = target.find("HTTPTargetConnection/LoadBalancer/Server")
    assert server is not None and server.get("name") == "genaipocs-catalog"
    assert target.findtext("HTTPTargetConnection/Path") == "/mcp"
    assert "https://" not in bundles["genaipocs-catalog"]["apiproxy/targets/default.xml"]


def test_bundles_are_reproducible(tmp_path: Path) -> None:
    files = bundle_files(
        "catalog",
        tools=["search_events"],
        issuer=SIGNER,
        jwks_uri="https://k",
        audience="a",
        scope="s",
    )
    first = write_bundle(tmp_path / "1", "catalog", files).read_bytes()
    second = write_bundle(tmp_path / "2", "catalog", files).read_bytes()

    assert first == second  # Mismo MD5: Terraform no sube revisiones sin cambios.


def test_tool_names_cannot_inject_into_the_condition() -> None:
    with pytest.raises(ValueError, match="no admitido"):
        bundle_files(
            "catalog", tools=["a|.*"], issuer=SIGNER, jwks_uri="https://k", audience="a", scope="s"
        )

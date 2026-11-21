"""Proxies de Apigee delante de cada servidor MCP (fase 6.G3, carril A en GCP, como APIM).

Un proxy por servidor (`genaipocs-<servidor>`), generado desde el contrato versionado para que
la lista de tools permitidas no se desalinee nunca:

1. `ParsePayload` (protocolo MCP): saca el método JSON-RPC y el nombre de la tool del cuerpo.
2. `VerifyJWT`: el token del propio servidor (el que firma el gateway con `signJwt`, 6.G2):
   firma con el JWKS de la cuenta, `iss`, `aud` del servidor y su `scope`.
3. `RaiseFault` si `tools/call` pide una tool que no está en el contrato del servidor.
4. `SpikeArrest` por cliente (`sub` del token): el `rate-limit-by-key` de APIM.

El servidor valida el token otra vez (defensa en profundidad). El destino es un TargetServer
(`genaipocs-<servidor>`) que crea Terraform: el bundle no lleva URLs y vale para cualquier entorno.

    uv run python -m genaipocs_mcp.apigee --signer <cuenta del gateway> --out .tmp/apigee
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from genaipocs_core.config import McpSettings, get_settings
from genaipocs_mcp.auth import SERVER_SCOPES
from genaipocs_mcp.contracts import CONTRACTS_DIR

PARSE = "ParsePayload-MCP"
VERIFY = "VerifyJWT-Server"
DENY = "RaiseFault-ToolNotAllowed"
SPIKE = "SpikeArrest-PerCustomer"
# Fecha fija en el zip: mismo contenido, mismo MD5 (Terraform solo sube una revisión si cambia).
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def proxy_name(server: str) -> str:
    return f"genaipocs-{server}"


def _tool_condition(tools: Sequence[str]) -> str:
    for tool in tools:
        if not _TOOL_NAME.fullmatch(tool):  # Va dentro de una regex: nada de metacaracteres.
            raise ValueError(f"Nombre de tool no admitido en el proxy: {tool!r}")
    allowed = "|".join(sorted(tools))
    method = f"parsepayload.{PARSE}.json-rpc.request.method"
    name = f"parsepayload.{PARSE}.json-rpc.request.params.name"
    return f'({method} = "tools/call") and !({name} ~~ "^({allowed})$")'


def bundle_files(
    server: str,
    *,
    tools: Sequence[str],
    issuer: str,
    jwks_uri: str,
    audience: str,
    scope: str,
    rate: str = "60pm",
) -> dict[str, str]:
    """Ficheros del bundle (`apiproxy/...` → XML) de un servidor."""
    name = proxy_name(server)
    post = 'request.verb = "POST"'
    files = {
        f"apiproxy/{name}.xml": f"""<APIProxy name={quoteattr(name)}>
  <Description>Servidor MCP {escape(server)} de Genaipocs (fase 6.G3)</Description>
</APIProxy>
""",
        "apiproxy/proxies/default.xml": f"""<ProxyEndpoint name="default">
  <PreFlow name="PreFlow">
    <Request>
      <Step><Name>{PARSE}</Name><Condition>{escape(post)}</Condition></Step>
      <Step><Name>{VERIFY}</Name></Step>
      <Step><Name>{DENY}</Name><Condition>{escape(_tool_condition(tools))}</Condition></Step>
      <Step><Name>{SPIKE}</Name></Step>
    </Request>
  </PreFlow>
  <HTTPProxyConnection>
    <BasePath>/{escape(name)}</BasePath>
    <Properties>
      <Property name="response.streaming.enabled">true</Property>
    </Properties>
  </HTTPProxyConnection>
  <RouteRule name="default">
    <TargetEndpoint>default</TargetEndpoint>
  </RouteRule>
</ProxyEndpoint>
""",
        "apiproxy/targets/default.xml": f"""<TargetEndpoint name="default">
  <HTTPTargetConnection>
    <LoadBalancer>
      <Server name={quoteattr(name)}/>
    </LoadBalancer>
    <Path>/mcp</Path>
    <Properties>
      <Property name="response.streaming.enabled">true</Property>
    </Properties>
  </HTTPTargetConnection>
</TargetEndpoint>
""",
        f"apiproxy/policies/{PARSE}.xml": f"""<ParsePayload name="{PARSE}">
  <PayloadType>JSON-RPC-2.0</PayloadType>
  <Protocol>MCP</Protocol>
</ParsePayload>
""",
        f"apiproxy/policies/{VERIFY}.xml": f"""<VerifyJWT name="{VERIFY}">
  <Algorithm>RS256</Algorithm>
  <PublicKey>
    <JWKS uri={quoteattr(jwks_uri)}/>
  </PublicKey>
  <Issuer>{escape(issuer)}</Issuer>
  <Audience>{escape(audience)}</Audience>
  <AdditionalClaims>
    <Claim name="scope">{escape(scope)}</Claim>
  </AdditionalClaims>
</VerifyJWT>
""",
        f"apiproxy/policies/{DENY}.xml": f"""<RaiseFault name="{DENY}">
  <FaultResponse>
    <Set>
      <StatusCode>403</StatusCode>
      <ReasonPhrase>Forbidden</ReasonPhrase>
      <Payload contentType="application/json">{{"error": "tool_not_allowed"}}</Payload>
    </Set>
  </FaultResponse>
</RaiseFault>
""",
        f"apiproxy/policies/{SPIKE}.xml": f"""<SpikeArrest name="{SPIKE}">
  <Rate>{escape(rate)}</Rate>
  <Identifier ref="jwt.{VERIFY}.claim.subject"/>
</SpikeArrest>
""",
    }
    return files


def write_bundle(out_dir: Path, server: str, files: Mapping[str, str]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{proxy_name(server)}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(files):
            bundle.writestr(zipfile.ZipInfo(name, date_time=_ZIP_DATE), files[name])
    return path


def contract_tools(server: str, contracts_dir: Path = CONTRACTS_DIR) -> list[str]:
    contract: dict[str, Any] = json.loads(
        (contracts_dir / f"{server}.json").read_text(encoding="utf-8")
    )
    return [tool["name"] for tool in contract["tools"]]


def build_all(out_dir: Path, *, signer: str, settings: McpSettings) -> list[Path]:
    from genaipocs_mcp.gcp import service_account_jwks_uri

    return [
        write_bundle(
            out_dir,
            server,
            bundle_files(
                server,
                tools=contract_tools(server),
                issuer=signer,
                jwks_uri=service_account_jwks_uri(signer),
                audience=settings.audience(server),
                scope=" ".join(scopes),
            ),
        )
        for server, scopes in SERVER_SCOPES.items()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bundles de Apigee de los servidores MCP")
    parser.add_argument("--signer", required=True, help="Cuenta de servicio del gateway (6.G2)")
    parser.add_argument("--out", type=Path, default=Path(".tmp/apigee"))
    args = parser.parse_args()
    for path in build_all(args.out, signer=args.signer, settings=get_settings().mcp):
        print(path)


if __name__ == "__main__":
    main()

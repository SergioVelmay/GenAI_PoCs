---
applyTo: "services/mcp_servers/**,services/support_agent/**"
description: "Servidores MCP con FastMCP, OAuth y MCP gateway (fases 6.2–6.4)."
---

# MCP (fases 6.2–6.4)

Doc: `docs/fase-6-tools-mcp.md`.

- Paquete `genaipocs_mcp` (FastMCP 4.x, SDK `mcp` 2.1.x por el pin de `strands-agents`). Servidores `catalog` (8101), `ticketing` (8102), `refunds` (8103) sobre `HttpTicketingClient`, con el mismo `ToolExecutor` que el bucle local (`RegistryTool`).
- Arranque: `uv run python -m genaipocs_mcp <servidor>` (Streamable HTTP en `/mcp`, `/health`). Contenedores: imagen única `services/mcp_servers/Dockerfile`; `docker compose --profile mcp up -d` (la API debe escuchar en 0.0.0.0:8000 del host).
- **Contratos versionados** en `services/mcp_servers/contracts/*.json`: nombre, descripción y esquema son la interfaz pública. Un cambio intencionado se regenera con `python -m genaipocs_mcp.contracts --update` y se revisa en el diff.
- Auth: `MCP__AUTH=jwt` convierte cada servidor en resource server (`genaipocs_mcp/auth.py`: `JWTVerifier` + `RemoteAuthProvider`, RFC 9728). Valida firma, `iss`, `exp`, `aud` por servidor (`api://genaipocs-mcp/{server}`, sin token passthrough) y scopes (`catalog:read`, `orders:read`, `refunds:write`). Identidad del claim `sub`.
- Sin OAuth, la identidad es `MCP__DEV_CUSTOMER_ID` y solo en `ENVIRONMENT=local`. Emisor de desarrollo: `python -m genaipocs_mcp.auth --customer cus_... --server ticketing` (claves en `.secrets/`).
- Gateway propio `genaipocs_mcp/gateway.py` (`python -m genaipocs_mcp gateway`, puerto 8100, exige `MCP__AUTH=jwt`): `ProxyProvider` por servidor, token exchange por servidor (`DevTokenExchanger` solo local; `EntraOboExchanger` en `genaipocs_mcp/entra.py` para Azure, que recibe el token de entrada como `assertion`), rate limit por usuario y auditoría. La API de ticketing que ven los contenedores es configurable con `MCP_TICKETING_API_URL` (por si el 8000 del host está ocupado).
- GCP (6.G2): `MCP__EXCHANGER=gcp` → `GcpTokenExchanger` (`genaipocs_mcp/gcp.py`): GCP no tiene OBO; el gateway firma con `signJwt` de IAM Credentials (cuenta `MCP__SIGNER_SERVICE_ACCOUNT`, sin claves) un JWT por servidor con `aud`, `scope`, `sub` = cliente y `act` = gateway. El token de entrada lo valida `gateway_auth` con `MCP__GATEWAY_AUTH__*` (Identity Platform: `aud` = proyecto, sin scopes, cliente en el claim `customer_id`); los servidores usan `MCP__ISSUER` = correo de la cuenta, el JWKS de la cuenta y `MCP__AUTHORIZATION_SERVER` = Identity Platform para RFC 9728. Valores: `terraform -chdir=infra/terraform/gcp output mcp_env`. Tests sin red con firmador RSA local y JWKS en memoria (`monkeypatch` de `JWTVerifier._fetch_jwks`).
- Carril A en Terraform, desactivado por defecto: `aws/mcp_gateway.tf` (`mcp_gateway_enabled`) y `azure/mcp_gateway.tf` (`apim_enabled`; APIM Developer ~50 $/mes).
- Agente mínimo carril B: `services/support_agent` (`McpToolset` → `ToolRegistry`; `SupportAgent` = `ToolLoop`). `uv run python -m support_agent --customer cus_... [--gateway] "pregunta"` tiene coste.

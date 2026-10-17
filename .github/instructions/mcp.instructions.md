---
applyTo: "services/mcp_servers/**,services/support_agent/**"
description: "Servidores MCP con FastMCP, OAuth y MCP gateway (fases 6.2–6.4)."
---

# MCP (fases 6.2–6.4)

Doc: `docs/fase-6-tools-mcp.md`.

- Paquete `headache_mcp` (FastMCP 4.x, SDK `mcp` 2.1.x por el pin de `strands-agents`). Servidores `catalog` (8101), `ticketing` (8102), `refunds` (8103) sobre `HttpTicketingClient`, con el mismo `ToolExecutor` que el bucle local (`RegistryTool`).
- Arranque: `uv run python -m headache_mcp <servidor>` (Streamable HTTP en `/mcp`, `/health`). Contenedores: imagen única `services/mcp_servers/Dockerfile`; `docker compose --profile mcp up -d` (la API debe escuchar en 0.0.0.0:8000 del host).
- **Contratos versionados** en `services/mcp_servers/contracts/*.json`: nombre, descripción y esquema son la interfaz pública. Un cambio intencionado se regenera con `python -m headache_mcp.contracts --update` y se revisa en el diff.
- Auth: `MCP__AUTH=jwt` convierte cada servidor en resource server (`headache_mcp/auth.py`: `JWTVerifier` + `RemoteAuthProvider`, RFC 9728). Valida firma, `iss`, `exp`, `aud` por servidor (`api://headache-mcp/{server}`, sin token passthrough) y scopes (`catalog:read`, `orders:read`, `refunds:write`). Identidad del claim `sub`.
- Sin OAuth, la identidad es `MCP__DEV_CUSTOMER_ID` y solo en `ENVIRONMENT=local`. Emisor de desarrollo: `python -m headache_mcp.auth --customer cus_... --server ticketing` (claves en `.secrets/`).
- Gateway propio `headache_mcp/gateway.py` (`python -m headache_mcp gateway`, puerto 8100, exige `MCP__AUTH=jwt`): `ProxyProvider` por servidor, token exchange por servidor (`DevTokenExchanger` solo local), rate limit por usuario y auditoría.
- Carril A en Terraform, desactivado por defecto: `aws/mcp_gateway.tf` (`mcp_gateway_enabled`) y `azure/mcp_gateway.tf` (`apim_enabled`; APIM Developer ~50 $/mes).
- Agente mínimo carril B: `services/support_agent` (`McpToolset` → `ToolRegistry`; `SupportAgent` = `ToolLoop`). `uv run python -m support_agent --customer cus_... [--gateway] "pregunta"` tiene coste.

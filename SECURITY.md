# Política de seguridad

## Qué es este repositorio

Son **pruebas de concepto didácticas**, no un servicio en producción. Los datos son sintéticos
(`data/synthetic/`) y el cliente, Genaipocs, es ficticio. Por defecto todo corre en local. Lo que
tiene coste en la nube está desactivado en Terraform (`*_enabled = false`) y nunca hay secretos en
el repo: `.env` local, OIDC en la CI e identidades gestionadas en la nube.

## Versiones con soporte

Solo la rama `main`. No hay versiones publicadas ni ramas de mantenimiento.

## Cómo informar de una vulnerabilidad

**No abras un issue público.** Usa el informe privado de GitHub: pestaña **Security** →
**Report a vulnerability**. Incluye:

- Qué componente está afectado (servicio, paquete, fichero de Terraform o workflow).
- Cómo reproducirlo y qué impacto tiene.
- Si lo conoces, cómo arreglarlo.

Es un proyecto personal: la respuesta es de buena fe y sin plazos garantizados. Lo habitual es un
primer acuse en una semana.

## Qué interesa especialmente

- Formas de saltarse el harness (inyección de prompts directa o indirecta, fugas de PII, llamadas
  a tools sin aprobación) que los tests de ataque (`rag_evals.attacks`, `rag_evals.redteam`) no
  detectan.
- Secretos o datos personales que se hayan colado en el historial.
- Permisos excesivos en los workflows de GitHub Actions o en el Terraform.

Queda fuera de alcance lo que solo se reproduce con configuración que no es la de por defecto y
está documentada como insegura (por ejemplo, `GUARDRAILS__FAIL_OPEN=true`).

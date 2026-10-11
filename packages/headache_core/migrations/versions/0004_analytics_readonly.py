"""Capa analítica de solo lectura para SQL generado por LLM (fase 8.2).

Vistas sin PII en el esquema `analytics` y un rol sin login que solo puede leerlas. El runner
entra con `SET LOCAL ROLE` dentro de una transacción READ ONLY; en la nube, una identidad propia
(IAM / Entra ID) miembro de este rol, sin contraseña.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "headache_analyst"

# Sin nombre, email, motivo libre del reembolso ni clave de idempotencia.
VIEWS = {
    "venues": "SELECT id, name, city, country, capacity FROM public.venues",
    "events": (
        "SELECT id, title, category, city, venue_id, price_amount, currency, refundable, "
        "refund_window_hours FROM public.events"
    ),
    "sessions": "SELECT id, event_id, starts_at, capacity, available FROM public.sessions",
    "customers": "SELECT id, country, language FROM public.customers",
    "orders": (
        "SELECT id, customer_id, session_id, quantity, total_amount, currency, status, "
        "created_at FROM public.orders"
    ),
    "refunds": ("SELECT id, order_id, amount, currency, status, requested_at FROM public.refunds"),
}


def upgrade() -> None:
    # Los roles son del clúster: puede existir ya por otra base de datos del mismo servidor.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        END
        $$
        """
    )
    op.execute("CREATE SCHEMA analytics")
    for name, query in VIEWS.items():
        op.execute(f"CREATE VIEW analytics.{name} AS {query}")
    op.execute(f"GRANT USAGE ON SCHEMA analytics TO {ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {ROLE}")
    op.execute(f"ALTER ROLE {ROLE} SET default_transaction_read_only = on")
    op.execute(f"ALTER ROLE {ROLE} SET statement_timeout = '5s'")
    # Quien ejecuta la migración (el usuario de la app) puede asumir el rol con SET ROLE.
    op.execute(f"GRANT {ROLE} TO CURRENT_USER")


def downgrade() -> None:
    op.execute("DROP SCHEMA analytics CASCADE")
    op.execute(f"REVOKE {ROLE} FROM CURRENT_USER")
    # El rol puede tener permisos en otras bases de datos del clúster: no se borra aquí.

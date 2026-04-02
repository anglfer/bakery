"""RF06: add unit dimension and base factor for automatic conversion

Revision ID: ab12cd34ef56
Revises: f6a7b8c9d0e1
Create Date: 2026-04-02 18:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ab12cd34ef56"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("unidad_medida", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "dimension",
                sa.String(length=20),
                nullable=False,
                server_default="CONTEO",
            )
        )
        batch_op.add_column(
            sa.Column(
                "factor_base",
                sa.Numeric(12, 4),
                nullable=False,
                server_default="1",
            )
        )
        batch_op.create_check_constraint(
            "ck_unidad_medida_factor_base_positivo", "factor_base > 0"
        )

    op.execute(
        """
        UPDATE unidad_medida
        SET dimension = CASE abreviatura
            WHEN 'kg' THEN 'MASA'
            WHEN 'g' THEN 'MASA'
            WHEN 'cos' THEN 'MASA'
            WHEN 'l' THEN 'VOLUMEN'
            WHEN 'ml' THEN 'VOLUMEN'
            WHEN 'pza' THEN 'CONTEO'
            ELSE 'CONTEO'
        END,
        factor_base = CASE abreviatura
            WHEN 'kg' THEN 1000
            WHEN 'g' THEN 1
            WHEN 'cos' THEN 25000
            WHEN 'l' THEN 1000
            WHEN 'ml' THEN 1
            WHEN 'pza' THEN 1
            ELSE 1
        END
        """
    )

    with op.batch_alter_table("unidad_medida", schema=None) as batch_op:
        batch_op.alter_column("dimension", server_default=None)
        batch_op.alter_column("factor_base", server_default=None)


def downgrade():
    with op.batch_alter_table("unidad_medida", schema=None) as batch_op:
        batch_op.drop_constraint(
            "ck_unidad_medida_factor_base_positivo",
            type_="check",
        )
        batch_op.drop_column("factor_base")
        batch_op.drop_column("dimension")

"""Add supplier contact and location fields for RF05

Revision ID: c3f5e7a9b1d2
Revises: b2d4f6a8c0e1
Create Date: 2026-04-02 10:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3f5e7a9b1d2"
down_revision = "b2d4f6a8c0e1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("proveedor", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "nombre_contacto",
                sa.String(length=120),
                nullable=False,
                server_default="Sin contacto",
            )
        )
        batch_op.add_column(
            sa.Column(
                "ciudad",
                sa.String(length=120),
                nullable=False,
                server_default="N/D",
            )
        )
        batch_op.add_column(
            sa.Column(
                "estado",
                sa.String(length=120),
                nullable=False,
                server_default="N/D",
            )
        )

    with op.batch_alter_table("proveedor", schema=None) as batch_op:
        batch_op.alter_column(
            "nombre_contacto",
            existing_type=sa.String(length=120),
            nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "ciudad",
            existing_type=sa.String(length=120),
            nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "estado",
            existing_type=sa.String(length=120),
            nullable=False,
            server_default=None,
        )


def downgrade():
    with op.batch_alter_table("proveedor", schema=None) as batch_op:
        batch_op.drop_column("estado")
        batch_op.drop_column("ciudad")
        batch_op.drop_column("nombre_contacto")

"""RF06: add movimiento inventario index and positive quantity constraint

Revision ID: f6a7b8c9d0e1
Revises: c3f5e7a9b1d2
Create Date: 2026-04-02 13:10:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "c3f5e7a9b1d2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("movimiento_inventario_mp", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_movimiento_mp_cantidad_positiva", "cantidad > 0"
        )
        batch_op.create_index(
            "ix_movimiento_mp_materia_fecha",
            ["id_materia_prima", "fecha"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("movimiento_inventario_mp", schema=None) as batch_op:
        batch_op.drop_index("ix_movimiento_mp_materia_fecha")
        batch_op.drop_constraint("ck_movimiento_mp_cantidad_positiva", type_="check")

"""Evidenca pošiljanja plačilnih pozivov.

Revision ID: 013_pozivi_clanarina
Revises: 012_pooblascene_vloge
"""

from alembic import op
import sqlalchemy as sa


revision = "013_pozivi_clanarina"
down_revision = "012_pooblascene_vloge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("clanarine") as batch_op:
        batch_op.add_column(
            sa.Column("poziv_poslan_ob", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("poziv_email", sa.String(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "poziv_status",
                sa.String(),
                nullable=False,
                server_default=sa.text("'ni_poslano'"),
            )
        )
        batch_op.add_column(
            sa.Column("poziv_napaka", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "poziv_poskusi",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("clanarine") as batch_op:
        batch_op.drop_column("poziv_poskusi")
        batch_op.drop_column("poziv_napaka")
        batch_op.drop_column("poziv_status")
        batch_op.drop_column("poziv_email")
        batch_op.drop_column("poziv_poslan_ob")

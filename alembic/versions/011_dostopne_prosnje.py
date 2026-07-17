"""Dostopne prošnje in povezava uporabnika s članom.

Revision ID: 011_dostopne_prosnje
Revises: 010
"""

from alembic import op
import sqlalchemy as sa


revision = "011_dostopne_prosnje"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("uporabniki") as batch_op:
        batch_op.add_column(sa.Column("clan_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_uporabniki_clan_id_clani",
            "clani",
            ["clan_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_uporabniki_clan_id",
            ["clan_id"],
            unique=True,
        )

    op.create_table(
        "dostopne_prosnje",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clan_id", sa.Integer(), nullable=False),
        sa.Column("uporabnisko_ime", sa.String(), nullable=False),
        sa.Column("geslo_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="caka"),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("opomba", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("obravnavano_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("obravnaval", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["clan_id"], ["clani.id"]),
    )
    op.create_index(
        "ix_dostopne_prosnje_clan_id",
        "dostopne_prosnje",
        ["clan_id"],
    )
    op.create_index(
        "ix_dostopne_prosnje_uporabnisko_ime",
        "dostopne_prosnje",
        ["uporabnisko_ime"],
    )
    op.create_index(
        "ix_dostopne_prosnje_status",
        "dostopne_prosnje",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dostopne_prosnje_status",
        table_name="dostopne_prosnje",
    )
    op.drop_index(
        "ix_dostopne_prosnje_uporabnisko_ime",
        table_name="dostopne_prosnje",
    )
    op.drop_index(
        "ix_dostopne_prosnje_clan_id",
        table_name="dostopne_prosnje",
    )
    op.drop_table("dostopne_prosnje")

    with op.batch_alter_table("uporabniki") as batch_op:
        batch_op.drop_index("ix_uporabniki_clan_id")
        batch_op.drop_constraint(
            "fk_uporabniki_clan_id_clani",
            type_="foreignkey",
        )
        batch_op.drop_column("clan_id")
"""Registracijske prošnje novih članov.

Revision ID: 012_registracijske_prosnje
Revises: 011_dostopne_prosnje
"""

from alembic import op
import sqlalchemy as sa


revision = "012_registracijske_prosnje"
down_revision = "011_dostopne_prosnje"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registracijske_prosnje",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("priimek", sa.String(), nullable=False),
        sa.Column("ime", sa.String(), nullable=False),
        sa.Column("klicni_znak", sa.String(), nullable=True),
        sa.Column("elektronska_posta", sa.String(), nullable=False),
        sa.Column("naslov_ulica", sa.String(), nullable=True),
        sa.Column("naslov_posta", sa.String(), nullable=True),
        sa.Column("mobilni_telefon", sa.String(), nullable=True),
        sa.Column("operaterski_razred", sa.String(), nullable=True),
        sa.Column("tip_clanstva", sa.String(), nullable=False),
        sa.Column("zrs_vrsta", sa.String(), nullable=False),
        sa.Column("uporabnisko_ime", sa.String(), nullable=False),
        sa.Column("geslo_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="caka"),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("opomba", sa.String(), nullable=True),
        sa.Column("created_clan_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("obravnavano_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("obravnaval", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["created_clan_id"], ["clani.id"]),
    )

    op.create_index(
        "ix_registracijske_prosnje_email",
        "registracijske_prosnje",
        ["elektronska_posta"],
    )
    op.create_index(
        "ix_registracijske_prosnje_klicni_znak",
        "registracijske_prosnje",
        ["klicni_znak"],
    )
    op.create_index(
        "ix_registracijske_prosnje_uporabnisko_ime",
        "registracijske_prosnje",
        ["uporabnisko_ime"],
    )
    op.create_index(
        "ix_registracijske_prosnje_status",
        "registracijske_prosnje",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_registracijske_prosnje_status",
        table_name="registracijske_prosnje",
    )
    op.drop_index(
        "ix_registracijske_prosnje_uporabnisko_ime",
        table_name="registracijske_prosnje",
    )
    op.drop_index(
        "ix_registracijske_prosnje_klicni_znak",
        table_name="registracijske_prosnje",
    )
    op.drop_index(
        "ix_registracijske_prosnje_email",
        table_name="registracijske_prosnje",
    )
    op.drop_table("registracijske_prosnje")
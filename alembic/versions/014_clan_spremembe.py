"""Samopostrezno preverjanje in popravljanje podatkov clanov.

Revision ID: 014_clan_spremembe
Revises: 013_pozivi_clanarina
"""

from alembic import op
import sqlalchemy as sa


revision = "014_clan_spremembe"
down_revision = "013_pozivi_clanarina"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clan_podatki_tokeni",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clan_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("uporabljen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["clan_id"], ["clani.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )

    op.create_index(
        "ix_clan_podatki_tokeni_clan_id",
        "clan_podatki_tokeni",
        ["clan_id"],
    )
    op.create_index(
        "ix_clan_podatki_tokeni_token_hash",
        "clan_podatki_tokeni",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "clan_spremembe",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clan_id", sa.Integer(), nullable=False),
        sa.Column("token_id", sa.Integer(), nullable=True),
        sa.Column("naslov_ulica", sa.String(), nullable=True),
        sa.Column("naslov_posta", sa.String(), nullable=True),
        sa.Column("elektronska_posta", sa.String(), nullable=True),
        sa.Column("mobilni_telefon", sa.String(), nullable=True),
        sa.Column("telefon_doma", sa.String(), nullable=True),
        sa.Column("podatki_pravilni", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=False, server_default="caka"),
        sa.Column("opomba_clana", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("obravnavano_at", sa.DateTime(), nullable=True),
        sa.Column("obravnaval", sa.String(), nullable=True),
        sa.Column("opomba_admin", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["clan_id"], ["clani.id"]),
        sa.ForeignKeyConstraint(["token_id"], ["clan_podatki_tokeni.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_clan_spremembe_clan_id",
        "clan_spremembe",
        ["clan_id"],
    )
    op.create_index(
        "ix_clan_spremembe_token_id",
        "clan_spremembe",
        ["token_id"],
    )
    op.create_index(
        "ix_clan_spremembe_status",
        "clan_spremembe",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_clan_spremembe_status", table_name="clan_spremembe")
    op.drop_index("ix_clan_spremembe_token_id", table_name="clan_spremembe")
    op.drop_index("ix_clan_spremembe_clan_id", table_name="clan_spremembe")
    op.drop_table("clan_spremembe")

    op.drop_index("ix_clan_podatki_tokeni_token_hash", table_name="clan_podatki_tokeni")
    op.drop_index("ix_clan_podatki_tokeni_clan_id", table_name="clan_podatki_tokeni")
    op.drop_table("clan_podatki_tokeni")

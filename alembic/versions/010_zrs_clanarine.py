"""ZRS ÄŤlanarine â€“ loÄŤen klubski in ZRS del.

Revision ID: 010
Revises: 009
Create Date: 2026-07-17
"""

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zrs_clanarine",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clan_id", sa.Integer(), nullable=False),
        sa.Column("leto", sa.Integer(), nullable=False),
        sa.Column("zrs_vrsta", sa.String(), nullable=False, server_default="Brez ZRS"),
        sa.Column("klub_znesek", sa.Float(), nullable=False, server_default="0"),
        sa.Column("zrs_znesek", sa.Float(), nullable=False, server_default="0"),
        sa.Column("datum_placila", sa.Date(), nullable=True),
        sa.Column("zrs_nakazano", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("datum_zrs_nakazila", sa.Date(), nullable=True),
        sa.Column("opombe", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["clan_id"], ["clani.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clan_id", "leto", name="uq_zrs_clan_leto"),
    )
    op.create_index("ix_zrs_clanarine_clan_id", "zrs_clanarine", ["clan_id"])
    op.create_index("ix_zrs_clanarine_leto", "zrs_clanarine", ["leto"])


def downgrade() -> None:
    op.drop_index("ix_zrs_clanarine_leto", table_name="zrs_clanarine")
    op.drop_index("ix_zrs_clanarine_clan_id", table_name="zrs_clanarine")
    op.drop_table("zrs_clanarine")
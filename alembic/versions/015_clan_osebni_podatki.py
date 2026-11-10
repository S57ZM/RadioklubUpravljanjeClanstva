"""Dodaj osebne podatke v zahtevke clanov."""

from alembic import op
import sqlalchemy as sa

revision = "015_clan_osebni_podatki"
down_revision = "014_clan_spremembe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clan_spremembe",
        sa.Column("ime", sa.String(), nullable=True),
    )
    op.add_column(
        "clan_spremembe",
        sa.Column("priimek", sa.String(), nullable=True),
    )
    op.add_column(
        "clan_spremembe",
        sa.Column("klicni_znak", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clan_spremembe", "klicni_znak")
    op.drop_column("clan_spremembe", "priimek")
    op.drop_column("clan_spremembe", "ime")

# Omeji uporabniške vloge na pooblaščene klubske funkcije.
#
# Revision ID: 012_pooblascene_vloge
# Revises: 011_dostopne_prosnje

from alembic import op


revision = "012_pooblascene_vloge"
down_revision = "011_dostopne_prosnje"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        "UPDATE uporabniki "
        "SET aktiven = 0 "
        "WHERE vloga NOT IN ("
        "'admin',"
        "'predsednik',"
        "'podpredsednik',"
        "'blagajnik'"
        ")"
    )
    op.execute(sql)


def downgrade() -> None:
    # Samodejna ponovna aktivacija starih računov ne bi bila varna.
    pass

"""login_poskusi – dodaj stolpec uporabnisko_ime za per-username rate limiting

Revision ID: 009
Revises: 008
Create Date: 2026-05-08
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("login_poskusi") as batch_op:
        batch_op.add_column(sa.Column("uporabnisko_ime", sa.String(), nullable=True))
    op.create_index("ix_login_poskusi_user_cas", "login_poskusi", ["uporabnisko_ime", "cas"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_login_poskusi_user_cas", table_name="login_poskusi")
    with op.batch_alter_table("login_poskusi") as batch_op:
        batch_op.drop_column("uporabnisko_ime")

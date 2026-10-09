"""Phase 3: automod_hits

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automod_hits",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("rule", sa.String(16), nullable=False),
        sa.Column("actions", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_automod_hits"),
    )
    op.create_index(
        "ix_automod_hits_guild_id_created_at", "automod_hits", ["guild_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("automod_hits")

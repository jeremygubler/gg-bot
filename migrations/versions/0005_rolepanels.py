"""Phase 6: role_panels, role_panel_items

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_panels",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("external", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_panels"),
    )
    op.create_index("ix_role_panels_guild_id", "role_panels", ["guild_id"])
    op.create_index("ix_role_panels_message_id", "role_panels", ["message_id"])
    op.create_table(
        "role_panel_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("panel_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(80), nullable=True),
        sa.Column("emoji", sa.String(64), nullable=True),
        sa.Column("emoji_key", sa.String(64), nullable=True),
        sa.Column("description", sa.String(100), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["panel_id"],
            ["role_panels.id"],
            name="fk_role_panel_items_panel_id_role_panels",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_panel_items"),
        sa.UniqueConstraint("panel_id", "role_id", name="uq_role_panel_items_panel_id_role_id"),
        sa.UniqueConstraint("panel_id", "emoji_key", name="uq_role_panel_items_panel_id_emoji_key"),
    )
    op.create_index("ix_role_panel_items_panel_id", "role_panel_items", ["panel_id"])


def downgrade() -> None:
    op.drop_table("role_panel_items")
    op.drop_table("role_panels")

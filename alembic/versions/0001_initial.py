"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("oauth_blob", sa.Text(), nullable=True),
        sa.Column("imap_host", sa.String(255), nullable=True),
        sa.Column("cursor", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_msg_id", sa.String(255), nullable=False),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("from_addr", sa.String(320), nullable=False),
        sa.Column("from_name", sa.String(320), nullable=True),
        sa.Column("to_addrs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("list_id", sa.String(255), nullable=True),
        sa.Column("list_unsub_http", sa.Text(), nullable=True),
        sa.Column("list_unsub_mailto", sa.String(320), nullable=True),
        sa.Column(
            "list_unsub_one_click", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("raw_headers", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "provider_msg_id", name="uq_message_account_msg"),
    )
    op.create_index("ix_message_thread_id", "message", ["thread_id"])
    op.create_index("ix_message_from_addr", "message", ["from_addr"])
    op.create_index("ix_message_date", "message", ["date"])
    op.create_index("ix_message_list_id", "message", ["list_id"])
    op.create_index("ix_message_account_date", "message", ["account_id", "date"])

    op.create_table(
        "thread",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("last_message_date", sa.DateTime(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_has_replied", sa.Boolean(), nullable=True),
        sa.UniqueConstraint("account_id", "thread_id", name="uq_thread_account_thread"),
    )

    op.create_table(
        "classification",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("message.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "sender",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("normalized_addr", sa.String(320), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("latest_label", sa.String(32), nullable=True),
        sa.Column("latest_confidence", sa.Float(), nullable=True),
        sa.Column("latest_reasoning", sa.Text(), nullable=True),
        sa.Column("classified_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("account_id", "normalized_addr", name="uq_sender_account_addr"),
    )
    op.create_index("ix_sender_normalized_addr", "sender", ["normalized_addr"])
    op.create_index("ix_sender_domain", "sender", ["domain"])

    op.create_table(
        "rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("match_kind", sa.String(16), nullable=False),
        sa.Column("pattern", sa.String(1024), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "action_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("message.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("performed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("undoable_until", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "kv_cache",
        sa.Column("key", sa.String(512), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("kv_cache")
    op.drop_table("task")
    op.drop_table("action_log")
    op.drop_table("rule")
    op.drop_index("ix_sender_domain", table_name="sender")
    op.drop_index("ix_sender_normalized_addr", table_name="sender")
    op.drop_table("sender")
    op.drop_table("classification")
    op.drop_table("thread")
    op.drop_index("ix_message_account_date", table_name="message")
    op.drop_index("ix_message_list_id", table_name="message")
    op.drop_index("ix_message_date", table_name="message")
    op.drop_index("ix_message_from_addr", table_name="message")
    op.drop_index("ix_message_thread_id", table_name="message")
    op.drop_table("message")
    op.drop_table("account")

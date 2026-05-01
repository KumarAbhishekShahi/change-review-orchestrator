"""Initial schema — reviews, review_findings, agent_results

Revision ID: 0001
Revises:
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("case_id",           sa.String(64),  primary_key=True),
        sa.Column("status",            sa.String(32),  nullable=False, server_default="QUEUED"),
        sa.Column("title",             sa.String(500), nullable=False),
        sa.Column("author",            sa.String(255), nullable=True),
        sa.Column("repository",        sa.String(255), nullable=True),
        sa.Column("branch",            sa.String(255), nullable=True),
        sa.Column("source_system",     sa.String(64),  nullable=True),
        sa.Column("source_ref",        sa.Text(),      nullable=True),
        sa.Column("commit_sha",        sa.String(64),  nullable=True),
        sa.Column("change_type",       sa.String(64),  nullable=False, server_default="FEATURE"),
        sa.Column("data_classification", sa.String(64), nullable=False, server_default="INTERNAL"),
        sa.Column("jira_ticket",       sa.String(64),  nullable=True),
        sa.Column("release_version",   sa.String(64),  nullable=True),
        sa.Column("has_breaking_changes", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("recommendation",    sa.String(32),  nullable=True),
        sa.Column("composite_score",   sa.Integer(),   nullable=True),
        sa.Column("findings_total",    sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("findings_critical", sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("findings_high",     sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("findings_medium",   sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("findings_low",      sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("deployment_strategy", sa.String(64), nullable=True),
        sa.Column("blast_consumers",   sa.String(32),  nullable=True),
        sa.Column("rollback_viable",   sa.Boolean(),   nullable=True),
        sa.Column("case_payload",      sa.JSON(),      nullable=True),
        sa.Column("agent_metadata",    sa.JSON(),      nullable=True),
        sa.Column("required_actions",  sa.JSON(),      nullable=True),
        sa.Column("advisory_actions",  sa.JSON(),      nullable=True),
        sa.Column("report_path",       sa.Text(),      nullable=True),
        sa.Column("bundle_path",       sa.Text(),      nullable=True),
        sa.Column("created_at",        sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at",      sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message",     sa.Text(),      nullable=True),
    )

    op.create_table(
        "review_findings",
        sa.Column("finding_id",       sa.String(64),  primary_key=True),
        sa.Column("case_id",          sa.String(64),
                   sa.ForeignKey("reviews.case_id", ondelete="CASCADE"),
                   nullable=False),
        sa.Column("agent",            sa.String(64),  nullable=False),
        sa.Column("category",         sa.String(64),  nullable=False),
        sa.Column("severity",         sa.String(32),  nullable=False),
        sa.Column("title",            sa.String(500), nullable=False),
        sa.Column("description",      sa.Text(),      nullable=True),
        sa.Column("remediation_guidance", sa.Text(),  nullable=True),
        sa.Column("policy_reference", sa.String(64),  nullable=True),
        sa.Column("affected_assets",  sa.JSON(),      nullable=True),
        sa.Column("suppressed",       sa.Boolean(),   nullable=False, server_default="false"),
        sa.Column("created_at",       sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_findings_case_id", "case_id"),
        sa.Index("ix_findings_severity", "severity"),
    )

    op.create_table(
        "agent_results",
        sa.Column("case_id",          sa.String(64),
                   sa.ForeignKey("reviews.case_id", ondelete="CASCADE"),
                   nullable=False),
        sa.Column("agent",            sa.String(64),  nullable=False),
        sa.Column("status",           sa.String(32),  nullable=False),
        sa.Column("summary",          sa.Text(),      nullable=True),
        sa.Column("duration_seconds", sa.Float(),     nullable=True),
        sa.Column("findings_count",   sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("metadata_blob",    sa.JSON(),      nullable=True),
        sa.Column("error_message",    sa.Text(),      nullable=True),
        sa.Column("created_at",       sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("case_id", "agent"),
    )


def downgrade() -> None:
    op.drop_table("agent_results")
    op.drop_table("review_findings")
    op.drop_table("reviews")

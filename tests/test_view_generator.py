"""
Tests for view DDL generation.

Tests the view-related methods in DDLGenerator.
"""

import pytest
from pathlib import Path

from src.schema.converter import PostgresViewDefinition, PostgresFieldDefinition
from src.schema.generator import DDLGenerator


@pytest.fixture
def sample_pg_view() -> PostgresViewDefinition:
    """Create sample PostgreSQL view definition."""
    return PostgresViewDefinition(
        view_name="ps_voucher_vw",
        postgres_sql_text="SELECT business_unit, voucher_id, invoice_id FROM ps_voucher WHERE voucher_status = 'C'",
        fields=[
            PostgresFieldDefinition(
                field_name="business_unit",
                postgres_type="VARCHAR(10)",
                is_nullable=True,
                is_key=False,
            ),
            PostgresFieldDefinition(
                field_name="voucher_id",
                postgres_type="VARCHAR(10)",
                is_nullable=True,
                is_key=False,
            ),
            PostgresFieldDefinition(
                field_name="invoice_id",
                postgres_type="VARCHAR(30)",
                is_nullable=True,
                is_key=False,
            ),
        ],
        comment="Voucher Summary View",
        dependencies=["ps_voucher"],
    )


@pytest.fixture
def sample_pg_view_dependent() -> PostgresViewDefinition:
    """Create sample view that depends on another view."""
    return PostgresViewDefinition(
        view_name="ps_voucher_summary_vw",
        postgres_sql_text="SELECT business_unit, COUNT(*) as cnt FROM ps_voucher_vw GROUP BY business_unit",
        fields=[
            PostgresFieldDefinition(
                field_name="business_unit",
                postgres_type="VARCHAR(10)",
                is_nullable=True,
                is_key=False,
            ),
            PostgresFieldDefinition(
                field_name="cnt",
                postgres_type="INTEGER",
                is_nullable=True,
                is_key=False,
            ),
        ],
        comment="Voucher Count by Business Unit",
        dependencies=["ps_voucher_vw"],
    )


class TestViewDDLGenerator:
    """Test DDLGenerator view methods."""

    def test_generate_create_view(self, sample_pg_view):
        """Test CREATE VIEW statement generation."""
        generator = DDLGenerator()
        ddl = generator.generate_create_view(sample_pg_view)

        assert "CREATE VIEW ps_voucher_vw AS" in ddl
        assert "SELECT business_unit, voucher_id, invoice_id FROM ps_voucher" in ddl
        assert ";" in ddl

    def test_generate_create_view_with_comment(self, sample_pg_view):
        """Test COMMENT ON VIEW statement generation."""
        generator = DDLGenerator()
        ddl = generator.generate_create_view(sample_pg_view)

        assert "-- Voucher Summary View" in ddl
        assert "COMMENT ON VIEW ps_voucher_vw IS 'Voucher Summary View'" in ddl

    def test_generate_drop_view(self):
        """Test DROP VIEW statement generation."""
        generator = DDLGenerator()
        ddl = generator.generate_drop_view("ps_voucher_vw")

        assert ddl == "DROP VIEW IF EXISTS ps_voucher_vw;"

    def test_generate_drop_view_without_if_exists(self):
        """Test DROP VIEW without IF EXISTS."""
        generator = DDLGenerator()
        ddl = generator.generate_drop_view("ps_voucher_vw", if_exists=False)

        assert ddl == "DROP VIEW ps_voucher_vw;"

    def test_save_view_ddl(self, sample_pg_view, temp_dir):
        """Test saving view DDL to file."""
        generator = DDLGenerator()
        generator.save_view_ddl(sample_pg_view, temp_dir)

        view_file = temp_dir / "views" / "ps_voucher_vw.sql"
        assert view_file.exists()

        content = view_file.read_text()
        assert "CREATE VIEW ps_voucher_vw" in content

    def test_dependency_ordering(self, sample_pg_view, sample_pg_view_dependent):
        """Test views are ordered by dependency."""
        generator = DDLGenerator()
        views = {
            "VOUCHER_VW": sample_pg_view,
            "VOUCHER_SUMMARY_VW": sample_pg_view_dependent,
        }

        ordered = generator._order_views_by_dependency(views)

        # ps_voucher_vw should come before ps_voucher_summary_vw
        view_names = [v.view_name for v in ordered]
        voucher_idx = view_names.index("ps_voucher_vw")
        summary_idx = view_names.index("ps_voucher_summary_vw")

        assert voucher_idx < summary_idx

    def test_generate_all_views_ddl(self, sample_pg_view, temp_dir):
        """Test generating all view DDL files."""
        generator = DDLGenerator()
        views = {"VOUCHER_VW": sample_pg_view}

        generator.generate_all_views_ddl(views, temp_dir)

        # Check individual file
        view_file = temp_dir / "views" / "ps_voucher_vw.sql"
        assert view_file.exists()

        # Check combined file
        all_views_file = temp_dir / "all_views.sql"
        assert all_views_file.exists()

        all_views_content = all_views_file.read_text()
        assert "CREATE VIEW ps_voucher_vw" in all_views_content
        assert "Generated by PS82-DB2-to-Postgres Migration Tool" in all_views_content

        # Check drop file
        drop_file = temp_dir / "drop_views.sql"
        assert drop_file.exists()

        drop_content = drop_file.read_text()
        assert "DROP VIEW" in drop_content

    def test_empty_sql_warning(self):
        """Test handling of view with empty SQL."""
        generator = DDLGenerator()
        empty_view = PostgresViewDefinition(
            view_name="ps_empty_vw",
            postgres_sql_text="",
            fields=[],
            comment=None,
            dependencies=[],
        )

        ddl = generator.generate_create_view(empty_view)

        # Should include warning comment
        assert "WARNING: Empty view definition" in ddl

    def test_sql_comment_escaping(self):
        """Test that single quotes in comments are escaped."""
        generator = DDLGenerator()
        view_with_quote = PostgresViewDefinition(
            view_name="ps_test_vw",
            postgres_sql_text="SELECT 1",
            fields=[],
            comment="View for 'testing' purposes",
            dependencies=[],
        )

        ddl = generator.generate_create_view(view_with_quote)

        # Single quote should be escaped
        assert "View for ''testing'' purposes" in ddl

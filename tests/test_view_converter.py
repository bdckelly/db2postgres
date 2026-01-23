"""
Tests for view SQL conversion from DB2 to PostgreSQL.

Tests the ViewSQLConverter class and view conversion methods in SchemaConverter.
"""

import pytest

from src.schema.converter import PostgresViewDefinition, SchemaConverter, ViewSQLConverter
from src.schema.extractor import ViewDefinition


class TestViewSQLConverter:
    """Test ViewSQLConverter class."""

    def test_lowercase_table_names(self):
        """Test PS_VOUCHER -> ps_voucher conversion."""
        converter = ViewSQLConverter(use_lowercase=True)
        sql = "SELECT * FROM PS_VOUCHER"
        result = converter.convert_sql(sql)
        assert result == "SELECT * FROM ps_voucher"

    def test_preserve_uppercase(self):
        """Test preserving uppercase when disabled."""
        converter = ViewSQLConverter(use_lowercase=False)
        sql = "SELECT * FROM PS_VOUCHER"
        result = converter.convert_sql(sql)
        assert "PS_VOUCHER" in result

    def test_substr_function(self):
        """Test SUBSTR -> SUBSTRING conversion."""
        converter = ViewSQLConverter()
        sql = "SELECT SUBSTR(FIELD1, 1, 5) FROM PS_TABLE"
        result = converter.convert_sql(sql)
        assert "SUBSTRING(" in result
        assert "SUBSTR(" not in result

    def test_value_to_coalesce(self):
        """Test VALUE -> COALESCE conversion."""
        converter = ViewSQLConverter()
        sql = "SELECT VALUE(FIELD1, 'default') FROM PS_TABLE"
        result = converter.convert_sql(sql)
        assert "COALESCE(" in result
        assert "VALUE(" not in result

    def test_year_function(self):
        """Test YEAR(date) -> EXTRACT(YEAR FROM date) conversion."""
        converter = ViewSQLConverter()
        sql = "SELECT YEAR(ADD_DT) FROM PS_VENDOR"
        result = converter.convert_sql(sql)
        assert "EXTRACT(YEAR FROM" in result
        assert "YEAR(" not in result

    def test_month_function(self):
        """Test MONTH(date) -> EXTRACT(MONTH FROM date) conversion."""
        converter = ViewSQLConverter()
        sql = "SELECT MONTH(ADD_DT) FROM PS_VENDOR"
        result = converter.convert_sql(sql)
        assert "EXTRACT(MONTH FROM" in result
        assert "MONTH(" not in result

    def test_day_function(self):
        """Test DAY(date) -> EXTRACT(DAY FROM date) conversion."""
        converter = ViewSQLConverter()
        sql = "SELECT DAY(ADD_DT) FROM PS_VENDOR"
        result = converter.convert_sql(sql)
        assert "EXTRACT(DAY FROM" in result
        assert "DAY(" not in result

    def test_current_date(self):
        """Test CURRENT DATE -> CURRENT_DATE conversion."""
        converter = ViewSQLConverter()
        sql = "SELECT * FROM PS_TABLE WHERE EFFDT <= CURRENT DATE"
        result = converter.convert_sql(sql)
        assert "CURRENT_DATE" in result
        assert "CURRENT DATE" not in result

    def test_current_timestamp(self):
        """Test CURRENT TIMESTAMP -> CURRENT_TIMESTAMP conversion."""
        converter = ViewSQLConverter()
        sql = "SELECT * FROM PS_TABLE WHERE DTTM_STAMP < CURRENT TIMESTAMP"
        result = converter.convert_sql(sql)
        assert "CURRENT_TIMESTAMP" in result
        assert "CURRENT TIMESTAMP" not in result

    def test_fetch_first_to_limit(self):
        """Test FETCH FIRST n ROWS ONLY -> LIMIT n conversion."""
        converter = ViewSQLConverter()
        sql = "SELECT * FROM PS_TABLE FETCH FIRST 100 ROWS ONLY"
        result = converter.convert_sql(sql)
        assert "LIMIT 100" in result
        assert "FETCH FIRST" not in result

    def test_remove_with_ur(self):
        """Test removal of WITH UR hint."""
        converter = ViewSQLConverter()
        sql = "SELECT * FROM PS_TABLE WITH UR"
        result = converter.convert_sql(sql)
        assert "WITH UR" not in result

    def test_remove_optimize_for(self):
        """Test removal of OPTIMIZE FOR hint."""
        converter = ViewSQLConverter()
        sql = "SELECT * FROM PS_TABLE OPTIMIZE FOR 100 ROWS"
        result = converter.convert_sql(sql)
        assert "OPTIMIZE FOR" not in result

    def test_complex_conversion(self, sample_view_with_functions):
        """Test complex view with multiple conversions needed."""
        converter = ViewSQLConverter()
        sql = sample_view_with_functions.db2_sql_text
        result = converter.convert_sql(sql)

        # All conversions should be applied
        assert "ps_vendor" in result  # lowercase
        assert "EXTRACT(YEAR FROM" in result  # YEAR function
        assert "SUBSTRING(" in result  # SUBSTR function
        assert "LIMIT 100" in result  # FETCH FIRST
        assert "WITH UR" not in result  # Removed

    def test_extract_dependencies(self):
        """Test dependency extraction from SQL."""
        converter = ViewSQLConverter()
        sql = "SELECT * FROM PS_VOUCHER A JOIN PS_VCHR_LINE B ON A.VOUCHER_ID = B.VOUCHER_ID"
        deps = converter.extract_dependencies(sql)

        assert "ps_voucher" in deps
        assert "ps_vchr_line" in deps
        assert len(deps) == 2

    def test_extract_dependencies_no_duplicates(self):
        """Test that dependency extraction removes duplicates."""
        converter = ViewSQLConverter()
        sql = "SELECT * FROM PS_VOUCHER WHERE EXISTS (SELECT 1 FROM PS_VOUCHER)"
        deps = converter.extract_dependencies(sql)

        assert deps.count("ps_voucher") == 1

    def test_empty_sql(self):
        """Test handling of empty SQL."""
        converter = ViewSQLConverter()
        result = converter.convert_sql("")
        assert result == ""


class TestSchemaConverterViews:
    """Test view conversion in SchemaConverter."""

    def test_convert_view(self, sample_view_def):
        """Test basic view conversion."""
        converter = SchemaConverter(use_lowercase=True)
        pg_view = converter.convert_view(sample_view_def)

        assert isinstance(pg_view, PostgresViewDefinition)
        assert pg_view.view_name == "ps_voucher_vw"
        assert "ps_voucher" in pg_view.postgres_sql_text
        assert pg_view.comment == "Voucher Summary View"

    def test_convert_view_preserves_dependencies(self, sample_view_def):
        """Test that dependencies are extracted during conversion."""
        converter = SchemaConverter(use_lowercase=True)
        pg_view = converter.convert_view(sample_view_def)

        assert "ps_voucher" in pg_view.dependencies

    def test_convert_view_with_functions(self, sample_view_with_functions):
        """Test view conversion with DB2 functions."""
        converter = SchemaConverter(use_lowercase=True)
        pg_view = converter.convert_view(sample_view_with_functions)

        # SQL should be converted
        assert "EXTRACT(YEAR FROM" in pg_view.postgres_sql_text
        assert "SUBSTRING(" in pg_view.postgres_sql_text
        assert "LIMIT 100" in pg_view.postgres_sql_text
        assert "WITH UR" not in pg_view.postgres_sql_text

    def test_convert_all_views(self, sample_view_def, sample_view_with_functions):
        """Test batch view conversion."""
        converter = SchemaConverter(use_lowercase=True)
        views = {
            "VOUCHER_VW": sample_view_def,
            "VENDOR_VW": sample_view_with_functions,
        }

        pg_views = converter.convert_all_views(views)

        assert len(pg_views) == 2
        assert "VOUCHER_VW" in pg_views
        assert "VENDOR_VW" in pg_views

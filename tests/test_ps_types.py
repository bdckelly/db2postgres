"""
Unit tests for PeopleSoft type mappings and utilities.
"""

import pytest
from datetime import date, datetime

from src.utils.ps_types import (
    PS_NULL_DATE,
    PS_NULL_DATETIME,
    convert_db2_type_to_postgres,
    convert_ps_null_date,
    is_effdt_field,
    is_null_date,
    is_null_datetime,
    is_special_table,
    suggest_chunk_key,
)


class TestTypeConversion:
    """Test DB2 to PostgreSQL type conversion."""

    def test_char_conversion(self):
        """Test CHAR type conversion."""
        result = convert_db2_type_to_postgres("CHAR", length=30)
        assert result == "VARCHAR(30)"

    def test_varchar_conversion(self):
        """Test VARCHAR type conversion."""
        result = convert_db2_type_to_postgres("VARCHAR", length=100)
        assert result == "VARCHAR(100)"

    def test_decimal_conversion(self):
        """Test DECIMAL type conversion."""
        result = convert_db2_type_to_postgres("DECIMAL", precision=15, scale=2)
        assert result == "NUMERIC(15,2)"

    def test_decimal_no_scale(self):
        """Test DECIMAL without scale."""
        result = convert_db2_type_to_postgres("DECIMAL", precision=10, scale=0)
        assert result == "NUMERIC(10,0)"

    def test_date_conversion(self):
        """Test DATE type conversion."""
        result = convert_db2_type_to_postgres("DATE")
        assert result == "DATE"

    def test_timestamp_conversion(self):
        """Test TIMESTAMP type conversion."""
        result = convert_db2_type_to_postgres("TIMESTAMP")
        assert result == "TIMESTAMP"

    def test_blob_conversion(self):
        """Test BLOB type conversion."""
        result = convert_db2_type_to_postgres("BLOB")
        assert result == "BYTEA"

    def test_clob_conversion(self):
        """Test CLOB type conversion."""
        result = convert_db2_type_to_postgres("CLOB")
        assert result == "TEXT"

    def test_unknown_type(self):
        """Test unknown type defaults to TEXT."""
        result = convert_db2_type_to_postgres("UNKNOWN_TYPE")
        assert result == "TEXT"


class TestEffdtHandling:
    """Test EFFDT field handling."""

    def test_is_effdt_field(self):
        """Test EFFDT field detection."""
        assert is_effdt_field("EFFDT") is True
        assert is_effdt_field("effdt") is True
        assert is_effdt_field("EFFDT_FROM") is True
        assert is_effdt_field("EFFDT_TO") is True
        assert is_effdt_field("OTHER_DATE") is False

    def test_is_null_date(self):
        """Test PS null date detection."""
        assert is_null_date(date(1900, 1, 1)) is True
        assert is_null_date(date(2025, 1, 1)) is False
        assert is_null_date(None) is False
        assert is_null_date("not a date") is False

    def test_is_null_datetime(self):
        """Test PS null datetime detection."""
        assert is_null_datetime(datetime(1900, 1, 1, 0, 0, 0)) is True
        assert is_null_datetime(datetime(2025, 1, 1, 12, 0, 0)) is False
        assert is_null_datetime(None) is False

    def test_convert_ps_null_date_preserve(self):
        """Test PS null date preservation."""
        null_date = date(1900, 1, 1)
        result = convert_ps_null_date(null_date, convert_to_null=False)
        assert result == null_date

    def test_convert_ps_null_date_to_none(self):
        """Test PS null date conversion to None."""
        null_date = date(1900, 1, 1)
        result = convert_ps_null_date(null_date, convert_to_null=True)
        assert result is None

    def test_convert_ps_null_date_normal_date(self):
        """Test normal date is not converted."""
        normal_date = date(2025, 1, 1)
        result = convert_ps_null_date(normal_date, convert_to_null=True)
        assert result == normal_date


class TestSpecialTables:
    """Test special table pattern detection."""

    def test_tree_table(self):
        """Test tree table detection."""
        result = is_special_table("PS_TREE_NODE")
        assert result is not None
        assert "tree" in result.lower()

    def test_xlat_table(self):
        """Test XLAT table detection."""
        result = is_special_table("PS_XLAT_TABLE")
        assert result is not None
        assert "translate" in result.lower()

    def test_setid_table(self):
        """Test SETID table detection."""
        result = is_special_table("PS_SETID_TBL")
        assert result is not None
        assert "setid" in result.lower()

    def test_normal_table(self):
        """Test normal table returns None."""
        result = is_special_table("PS_VOUCHER")
        assert result is None


class TestChunkKeySelection:
    """Test chunk key suggestion logic."""

    def test_suggest_effdt_key(self):
        """Test EFFDT is preferred for chunking."""
        keys = ["BUSINESS_UNIT", "EFFDT", "VOUCHER_ID"]
        result = suggest_chunk_key(keys)
        assert result == "EFFDT"

    def test_suggest_good_key(self):
        """Test known good key selection."""
        keys = ["BUSINESS_UNIT", "EMPLID"]
        result = suggest_chunk_key(keys)
        assert result == "EMPLID"

    def test_suggest_first_key(self):
        """Test fallback to first key."""
        keys = ["UNKNOWN_KEY1", "UNKNOWN_KEY2"]
        result = suggest_chunk_key(keys)
        assert result == "UNKNOWN_KEY1"

    def test_suggest_no_keys(self):
        """Test empty key list."""
        result = suggest_chunk_key([])
        assert result is None


class TestConstants:
    """Test constant values."""

    def test_ps_null_date_value(self):
        """Test PS_NULL_DATE constant."""
        assert PS_NULL_DATE == date(1900, 1, 1)

    def test_ps_null_datetime_value(self):
        """Test PS_NULL_DATETIME constant."""
        assert PS_NULL_DATETIME == datetime(1900, 1, 1, 0, 0, 0)

"""Row-set comparison: counts, checksums, keyed and unkeyed diffs, normalisation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sproc_to_lakehouse.parity.compare import RowSet, compare, normalize


def test_identical_sets_match_regardless_of_row_order():
    left = RowSet(["id", "v"], [(1, "a"), (2, "b")])
    right = RowSet(["ID", "V"], [(2, "b"), (1, "a")])
    result = compare("t", left, right, key=["id"])
    assert result.match and all(c.match for c in result.columns)


def test_value_difference_is_reported_by_key():
    left = RowSet(["id", "v"], [(1, "a"), (2, "b")])
    right = RowSet(["id", "v"], [(1, "a"), (2, "X")])
    result = compare("t", left, right, key=["id"])
    assert not result.match
    assert result.value_diff_count == 1 and result.rows_with_value_diffs == 1
    diff = result.value_diffs[0]
    assert (diff.key, diff.column, diff.sqlserver, diff.spark) == ({"id": "2"}, "v", "b", "X")
    assert [c.match for c in result.columns] == [True, False]


def test_missing_and_extra_rows_are_reported():
    left = RowSet(["id"], [(1,), (2,)])
    right = RowSet(["id"], [(2,), (3,)])
    result = compare("t", left, right, key=["id"])
    assert result.only_in_sqlserver == [{"id": "1"}] and result.only_in_spark == [{"id": "3"}]


def test_without_key_rows_compare_as_a_multiset():
    left = RowSet(["a"], [("x",), ("x",)])
    right = RowSet(["a"], [("x",)])
    result = compare("t", left, right)
    assert not result.match and result.only_in_sqlserver_count == 1 and result.key == []


def test_ignored_columns_are_left_out():
    left = RowSet(["id", "loaded_at"], [(1, datetime(2024, 1, 1))])
    right = RowSet(["id", "loaded_at"], [(1, datetime(2024, 1, 2))])
    result = compare("t", left, right, key=["id"], ignore=["loaded_at"])
    assert result.match and result.ignored == ["loaded_at"]


def test_column_sets_must_agree():
    result = compare("t", RowSet(["id", "a"], [(1, 1)]), RowSet(["id", "b"], [(1, 1)]))
    assert not result.match and result.column_mismatch == ["a", "b"]


def test_normalisation():
    assert normalize(Decimal("12.30")) == normalize(Decimal("12.3")) == "12.3"
    assert normalize(Decimal("1.005"), scale=2) == "1.01"
    assert normalize(Decimal("0.00")) == normalize(0) == "0"
    assert normalize("AB  ") == "AB"
    assert normalize(date(2024, 3, 1)) == "2024-03-01"
    assert normalize(datetime(2024, 3, 1, 8, 5)) == "2024-03-01 08:05:00"
    assert normalize(True) is True and normalize(None) is None


def test_decimal_scale_rounding_applies_to_both_sides():
    left = RowSet(["id", "x"], [(1, Decimal("2.345"))])
    right = RowSet(["id", "x"], [(1, Decimal("2.35"))])
    assert compare("t", left, right, key=["id"], scales={"x": 2}).match
    assert not compare("t", left, right, key=["id"]).match

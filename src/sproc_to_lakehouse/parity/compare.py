"""Compare two row sets: row counts, per-column checksums and row-level differences.

Values are normalised before comparing so that representation differences between the
two drivers do not count as mismatches:

* decimals compare by value (``12.30`` equals ``12.3``), rounded to the column scale
  when the column type is known;
* trailing spaces are ignored (SQL Server pads CHAR columns and ignores trailing spaces
  in comparisons);
* dates and timestamps compare as ISO strings; BIT and BOOLEAN both become True/False.

Everything else must match exactly. Any normalisation is listed in the report.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

NORMALISATION_NOTES = [
    "decimals compared by value, rounded to the column's scale when known",
    "trailing spaces ignored (CHAR padding)",
    "dates/timestamps compared as ISO-8601 text; BIT and BOOLEAN as true/false",
]
MAX_LISTED = 25


@dataclass
class RowSet:
    columns: list[str]
    rows: list[tuple]


@dataclass
class ColumnCheck:
    name: str
    sqlserver: str
    spark: str

    @property
    def match(self) -> bool:
        return self.sqlserver == self.spark


@dataclass
class ValueDiff:
    key: dict[str, Any]
    column: str
    sqlserver: Any
    spark: Any


@dataclass
class Comparison:
    name: str
    key: list[str]
    ignored: list[str]
    sqlserver_rows: int
    spark_rows: int
    columns: list[ColumnCheck] = field(default_factory=list)
    only_in_sqlserver: list[dict[str, Any]] = field(default_factory=list)
    only_in_spark: list[dict[str, Any]] = field(default_factory=list)
    value_diffs: list[ValueDiff] = field(default_factory=list)
    only_in_sqlserver_count: int = 0
    only_in_spark_count: int = 0
    value_diff_count: int = 0
    rows_with_value_diffs: int = 0
    column_mismatch: list[str] = field(default_factory=list)  # column sets that differ

    @property
    def match(self) -> bool:
        return (
            self.sqlserver_rows == self.spark_rows
            and not self.column_mismatch
            and self.only_in_sqlserver_count == 0
            and self.only_in_spark_count == 0
            and self.value_diff_count == 0
            and all(c.match for c in self.columns)
        )


def normalize(value: Any, scale: int | None = None) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        number = Decimal(str(value))
        if scale is not None:
            number = number.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
        normalized = number.normalize()
        return "0" if normalized.is_zero() else format(normalized, "f")
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value.rstrip(" ")
    return value


def _checksum(values: list[Any]) -> str:
    rendered = sorted("\x00" if v is None else repr(v) for v in values)
    return hashlib.sha256("\x1f".join(rendered).encode()).hexdigest()[:12]


def compare(
    name: str,
    sqlserver: RowSet,
    spark: RowSet,
    *,
    key: list[str] | None = None,
    ignore: list[str] | None = None,
    scales: dict[str, int] | None = None,
) -> Comparison:
    """Compare SQL Server and Spark rows. Columns are matched by (case-insensitive) name."""
    ignore = [c.lower() for c in ignore or []]
    scales = {k.lower(): v for k, v in (scales or {}).items()}
    left_cols = [c.lower() for c in sqlserver.columns]
    right_cols = [c.lower() for c in spark.columns]
    result = Comparison(
        name=name,
        key=[k.lower() for k in key or []],
        ignored=[c for c in left_cols if c in ignore],
        sqlserver_rows=len(sqlserver.rows),
        spark_rows=len(spark.rows),
    )
    if set(left_cols) != set(right_cols):
        result.column_mismatch = sorted(set(left_cols) ^ set(right_cols))
    columns = [c for c in left_cols if c in right_cols and c not in ignore]

    def records(rowset: RowSet, cols: list[str]) -> list[dict[str, Any]]:
        index = {c: i for i, c in enumerate(cols)}
        return [
            {c: normalize(row[index[c]], scales.get(c)) for c in columns} for row in rowset.rows
        ]

    left, right = records(sqlserver, left_cols), records(spark, right_cols)
    for column in columns:
        result.columns.append(
            ColumnCheck(
                column, _checksum([r[column] for r in left]), _checksum([r[column] for r in right])
            )
        )

    key_cols = [k for k in result.key if k in columns]
    if key_cols and len(key_cols) == len(result.key):
        _diff_by_key(result, left, right, key_cols, columns)
    else:
        result.key = []
        _diff_as_multiset(result, left, right, columns)
    return result


def _diff_by_key(result: Comparison, left, right, key: list[str], columns: list[str]) -> None:
    def index(rows):
        out: dict[tuple, dict] = {}
        for row in rows:
            out.setdefault(tuple(row[k] for k in key), row)
        return out

    lmap, rmap = index(left), index(right)
    only_left = [lmap[k] for k in lmap if k not in rmap]
    only_right = [rmap[k] for k in rmap if k not in lmap]
    result.only_in_sqlserver_count, result.only_in_spark_count = len(only_left), len(only_right)
    result.only_in_sqlserver, result.only_in_spark = only_left[:MAX_LISTED], only_right[:MAX_LISTED]
    for k in sorted(set(lmap) & set(rmap), key=lambda t: tuple(str(v) for v in t)):
        if any(lmap[k][c] != rmap[k][c] for c in columns):
            result.rows_with_value_diffs += 1
        for column in columns:
            if lmap[k][column] != rmap[k][column]:
                result.value_diff_count += 1
                if len(result.value_diffs) < MAX_LISTED:
                    result.value_diffs.append(
                        ValueDiff(
                            dict(zip(key, k, strict=True)), column, lmap[k][column], rmap[k][column]
                        )
                    )


def _diff_as_multiset(result: Comparison, left, right, columns: list[str]) -> None:
    def as_tuple(row):
        return tuple(row[c] for c in columns)

    lcount, rcount = Counter(map(as_tuple, left)), Counter(map(as_tuple, right))
    only_left = list((lcount - rcount).elements())
    only_right = list((rcount - lcount).elements())
    result.only_in_sqlserver_count, result.only_in_spark_count = len(only_left), len(only_right)
    result.only_in_sqlserver = [dict(zip(columns, r, strict=True)) for r in only_left[:MAX_LISTED]]
    result.only_in_spark = [dict(zip(columns, r, strict=True)) for r in only_right[:MAX_LISTED]]

"""Generator rewrites the analyzer's classifications depend on.

Some constructs are only ``auto``/``auto_with_note`` because the code generator fixes
what sqlglot's plain transpilation gets wrong. Every such rewrite is named here; rules
reference these constants in :attr:`Finding.rewrite`, and the generator (Phase 2) must
implement each one. A test enforces that, so the analyzer cannot over-promise.
"""

from __future__ import annotations

TRANSIENT_TABLE_TO_CTE = "transient_table_to_cte"
UPDATE_FROM_TO_MERGE = "update_from_to_merge"
PLUS_TO_CONCAT = "plus_to_concat"
TRY_CONVERT_SAFE_PARSE = "try_convert_safe_parse"
ISO_WEEK_EXTRACT = "iso_week_extract"
BIT_TO_BOOLEAN = "bit_to_boolean"
COMPACT_DATE_LITERAL = "compact_date_literal"

REWRITES: dict[str, str] = {
    TRANSIENT_TABLE_TO_CTE: "Temp tables / table variables populated without later mutation "
    "become CTEs (or intermediate models when read more than once).",
    UPDATE_FROM_TO_MERGE: "UPDATE/DELETE ... FROM ... JOIN becomes MERGE INTO, because Delta "
    "UPDATE/DELETE has no FROM clause.",
    PLUS_TO_CONCAT: "String '+' becomes CONCAT; sqlglot leaves '+', which Databricks treats as "
    "numeric addition.",
    TRY_CONVERT_SAFE_PARSE: "TRY_CONVERT with a style becomes CAST(TRY_TO_TIMESTAMP(...)); "
    "sqlglot emits TO_DATE/TO_TIMESTAMP, which raise on bad input.",
    ISO_WEEK_EXTRACT: "EXTRACT(WEEKISO ...) becomes EXTRACT(WEEK ...), the Databricks ISO 8601 "
    "field.",
    BIT_TO_BOOLEAN: "BIT columns are BOOLEAN on Databricks: 0/1 comparisons become FALSE/TRUE "
    "and values written into them are cast to BOOLEAN (needs DDL).",
    COMPACT_DATE_LITERAL: "'yyyymmdd' strings compared with dates become DATE literals; Spark "
    "cannot cast them implicitly (needs DDL).",
}

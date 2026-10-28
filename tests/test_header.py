from __future__ import annotations

import pytest

from sproc_to_lakehouse.models import Parameter
from sproc_to_lakehouse.parser import ProcedureParseError, parse_procedures
from sproc_to_lakehouse.parser.header import split_batches
from sproc_to_lakehouse.parser.lexer import tokenize


def test_parses_name_schema_and_parameters():
    (proc,) = parse_procedures(
        """
        CREATE OR ALTER PROCEDURE [dbo].[usp_demo]
            @run_date DATE = NULL,
            @limit    INT = 100,
            @label    NVARCHAR(50) = N'all',
            @total    DECIMAL(12, 2) OUTPUT
        AS
        SELECT 1
        """
    )
    assert (proc.schema, proc.name) == ("dbo", "usp_demo")
    assert proc.parameters == [
        Parameter("@run_date", "DATE", "NULL"),
        Parameter("@limit", "INT", "100"),
        Parameter("@label", "NVARCHAR(50)", "N'all'"),
        Parameter("@total", "DECIMAL(12, 2)", None, is_output=True),
    ]


def test_parenthesised_parameter_list_and_procedure_options():
    (proc,) = parse_procedures(
        "CREATE PROC usp_x (@a INT, @b AS VARCHAR(10) OUT) "
        "WITH RECOMPILE, EXECUTE AS OWNER AS SELECT 1"
    )
    assert proc.schema is None
    assert [p.name for p in proc.parameters] == ["@a", "@b"]
    assert proc.parameters[1].is_output
    assert len(proc.statements()) == 1


def test_procedure_without_parameters():
    (proc,) = parse_procedures("CREATE PROCEDURE dbo.usp_none AS BEGIN SELECT 1 END")
    assert proc.parameters == []


def test_go_splits_batches_and_non_procedure_batches_are_ignored():
    sql = (
        "SET ANSI_NULLS ON\nGO\n"
        "CREATE PROCEDURE a AS SELECT 1\nGO\n"
        "CREATE PROCEDURE b AS SELECT 2\nGO"
    )
    assert [p.name for p in parse_procedures(sql)] == ["a", "b"]


def test_go_inside_a_line_is_not_a_batch_separator():
    tokens = tokenize("SELECT go FROM t\nGO\nSELECT 1")
    assert len(split_batches(tokens)) == 2


def test_missing_as_is_reported_with_line():
    with pytest.raises(ProcedureParseError, match="line 1: expected AS"):
        parse_procedures("CREATE PROCEDURE dbo.broken @a INT SELECT 1")


def test_source_sql_and_line_are_recorded():
    sql = "-- header comment\n\nCREATE PROCEDURE dbo.p AS\nSELECT 1\nGO\n"
    (proc,) = parse_procedures(sql, source_path="x.sql")
    assert proc.line == 3
    assert proc.source_sql.startswith("CREATE PROCEDURE")
    assert proc.source_path == "x.sql"

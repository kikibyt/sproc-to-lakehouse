"""Statement classification, sqlglot parsing, and table read/write extraction."""

from __future__ import annotations

import pytest

from sproc_to_lakehouse.models import StatementKind, TableKind, TableRef

K = StatementKind


def only_statement(proc, body: str, params: str = ""):
    (stmt,) = proc(body, params).statements()
    return stmt


def names(refs) -> list[str]:
    return [r.qualified_name for r in refs]


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("SELECT a INTO #t FROM dbo.s", K.SELECT_INTO),
        ("SELECT @x = MAX(a) FROM dbo.s", K.SELECT_ASSIGN),
        ("DECLARE @t TABLE (a INT)", K.DECLARE_TABLE_VARIABLE),
        ("DECLARE @a INT, @b DATE", K.DECLARE_VARIABLE),
        ("SET @a = 1", K.SET_VARIABLE),
        ("SET XACT_ABORT ON", K.SET_OPTION),
        ("EXEC dbo.other @p = 1", K.EXEC_PROCEDURE),
        ("EXEC @rc = dbo.other", K.EXEC_PROCEDURE),
        ("EXEC (@sql)", K.EXEC_DYNAMIC),
        ("EXECUTE sp_executesql @sql", K.EXEC_DYNAMIC),
        ("EXEC @proc_name", K.EXEC_DYNAMIC),
        ("CREATE TABLE #t (a INT)", K.CREATE_TABLE),
        ("CREATE NONCLUSTERED INDEX ix ON #t (a)", K.CREATE_INDEX),
        ("TRUNCATE TABLE dbo.t", K.TRUNCATE),
        ("BEGIN TRAN", K.BEGIN_TRANSACTION),
        ("RAISERROR('x', 16, 1)", K.RAISERROR),
        ("PRINT 'hello'", K.PRINT),
        ("RETURN 1", K.RETURN),
        ("WAITFOR DELAY '00:00:01'", K.OTHER),
    ],
)
def test_classification(proc, body, kind):
    assert (
        only_statement(
            proc, body, "@x INT, @a INT, @rc INT, @sql NVARCHAR(MAX), @proc_name SYSNAME"
        ).kind
        is kind
    )


def test_insert_select_reads_and_writes(proc):
    stmt = only_statement(
        proc, "INSERT INTO dbo.target (a) SELECT s.a FROM dbo.src s JOIN #tmp t ON t.a = s.a"
    )
    assert stmt.parsed
    assert names(stmt.writes) == ["dbo.target"]
    assert names(stmt.reads) == ["dbo.src", "#tmp"]
    assert stmt.reads[1].kind is TableKind.TEMP


def test_update_alias_is_resolved_to_the_real_table(proc):
    stmt = only_statement(
        proc, "UPDATE s SET s.a = x.a FROM dbo.shipments AS s JOIN dbo.events AS x ON x.id = s.id"
    )
    assert names(stmt.writes) == ["dbo.shipments"]
    assert names(stmt.reads) == ["dbo.events"]


def test_delete_with_join_does_not_report_the_alias_as_a_read(proc):
    stmt = only_statement(proc, "DELETE d FROM dbo.dispenses d JOIN dbo.x x ON x.id = d.id")
    assert names(stmt.writes) == ["dbo.dispenses"]
    assert names(stmt.reads) == ["dbo.x"]


def test_cte_names_are_not_tables(proc):
    stmt = only_statement(proc, "WITH recent AS (SELECT * FROM dbo.orders) SELECT * FROM recent")
    assert names(stmt.reads) == ["dbo.orders"]


def test_table_variables_and_select_into_targets(proc):
    p = proc(
        "DECLARE @t TABLE (a INT)\n"
        "INSERT INTO @t (a) SELECT a FROM dbo.s\n"
        "SELECT a INTO #copy FROM @t"
    )
    _, insert, select_into = p.statements()
    assert insert.writes == (TableRef("@t", TableKind.TABLE_VARIABLE),)
    assert select_into.writes == (TableRef("#copy", TableKind.TEMP),)
    assert select_into.reads == (TableRef("@t", TableKind.TABLE_VARIABLE),)


def test_merge_target_and_source(proc):
    stmt = only_statement(
        proc,
        "MERGE dbo.dim AS t USING dbo.stg AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.a = s.a;",
    )
    assert names(stmt.writes) == ["dbo.dim"]
    assert names(stmt.reads) == ["dbo.stg"]


def test_ddl_targets_are_not_reads(proc):
    p = proc("CREATE TABLE #t (a INT)\nDROP TABLE IF EXISTS #t")
    create, drop = p.statements()
    assert create.ddl_target == drop.ddl_target == TableRef("#t", TableKind.TEMP)
    assert create.reads == drop.reads == ()


def test_cursor_query_is_parsed_for_reads(proc):
    stmt = only_statement(proc, "DECLARE c CURSOR FOR SELECT id FROM dbo.products FOR READ ONLY")
    assert names(stmt.reads) == ["dbo.products"]


def test_delete_top_is_not_trusted_to_sqlglot_but_targets_are_recovered(proc):
    stmt = only_statement(
        proc,
        "DELETE TOP (100) FROM dbo.dispenses OUTPUT DELETED.id INTO dbo.archive (id) WHERE id < 5",
    )
    assert stmt.expression is None
    assert "TOP" in stmt.parse_error
    assert names(stmt.writes) == ["dbo.dispenses", "dbo.archive"]


def test_cursor_commands_are_never_handed_to_sqlglot(proc):
    # sqlglot parses "OPEN c" as a column alias; it must not be treated as parsed SQL.
    p = proc("DECLARE c CURSOR FOR SELECT 1\nOPEN c")
    open_stmt = p.statements()[1]
    assert open_stmt.kind is K.OPEN_CURSOR
    assert open_stmt.expression is None and open_stmt.parse_error is None


def test_conditions_are_parsed_with_their_reads(proc):
    p = proc("IF NOT EXISTS (SELECT 1 FROM dbo.flags WHERE f = 1) RETURN")
    (node,) = p.body
    assert node.condition.expression is not None
    assert names(node.condition.reads) == ["dbo.flags"]

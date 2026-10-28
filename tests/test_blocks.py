"""Statement splitting and control-flow structure, with and without semicolons."""

from __future__ import annotations

import pytest

from sproc_to_lakehouse.models import (
    Block,
    IfNode,
    Statement,
    StatementKind,
    TryCatchNode,
    WhileNode,
)
from sproc_to_lakehouse.parser import ProcedureParseError

K = StatementKind


def kinds(procedure) -> list[StatementKind]:
    return [s.kind for s in procedure.statements()]


def test_statements_split_without_semicolons(proc):
    p = proc(
        """
        SET NOCOUNT ON
        DECLARE @x INT = 1
        SELECT @x = COUNT(*) FROM dbo.a
        UPDATE dbo.a SET v = @x WHERE id = 1
        DELETE FROM dbo.b WHERE id = @x
        """
    )
    assert kinds(p) == [K.SET_OPTION, K.DECLARE_VARIABLE, K.SELECT_ASSIGN, K.UPDATE, K.DELETE]


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("INSERT INTO dbo.t (a) SELECT a FROM dbo.s", K.INSERT),
        ("INSERT INTO dbo.t (a) SELECT a FROM dbo.s UNION ALL SELECT a FROM dbo.u", K.INSERT),
        ("INSERT INTO dbo.t EXEC dbo.other_proc", K.INSERT),
        ("WITH c AS (SELECT 1 AS a) INSERT INTO dbo.t (a) SELECT a FROM c", K.INSERT),
        ("WITH c (a) AS (SELECT 1) UPDATE dbo.t SET a = 1 FROM dbo.t JOIN c ON 1 = 1", K.UPDATE),
        ("SELECT a FROM dbo.t WITH (NOLOCK) WHERE a > 1", K.SELECT),
        ("SELECT TOP (5) WITH TIES a FROM dbo.t ORDER BY a", K.SELECT),
        ("SELECT a FROM dbo.t ORDER BY a OFFSET 5 ROWS FETCH NEXT 5 ROWS ONLY", K.SELECT),
        ("DROP TABLE IF EXISTS #t", K.DROP_TABLE),
        ("DECLARE c CURSOR LOCAL FOR SELECT a FROM dbo.t FOR UPDATE OF a", K.DECLARE_CURSOR),
        ("SELECT CASE WHEN a = 1 THEN 'x' ELSE 'y' END AS z FROM dbo.t", K.SELECT),
    ],
)
def test_continuation_keywords_stay_in_one_statement(proc, body, kind):
    p = proc(body)
    assert kinds(p) == [kind]


def test_merge_runs_to_its_terminating_semicolon(proc):
    p = proc(
        """
        MERGE dbo.t AS tgt USING dbo.s AS src ON tgt.id = src.id
        WHEN MATCHED THEN UPDATE SET tgt.a = src.a
        WHEN NOT MATCHED THEN INSERT (id, a) VALUES (src.id, src.a)
        WHEN NOT MATCHED BY SOURCE THEN DELETE;
        SELECT 1
        """
    )
    assert kinds(p) == [K.MERGE, K.SELECT]


def test_merge_without_semicolon_is_an_error(proc):
    with pytest.raises(ProcedureParseError, match="MERGE must be terminated"):
        proc("MERGE dbo.t USING dbo.s ON 1 = 1 WHEN MATCHED THEN DELETE")


def test_if_else_with_semicolon_before_else(proc):
    p = proc(
        "IF EXISTS (SELECT 1 FROM dbo.t) UPDATE dbo.t SET a = 1; "
        "ELSE INSERT INTO dbo.t (a) VALUES (1);"
    )
    (node,) = p.body
    assert isinstance(node, IfNode)
    assert node.condition.sql == "EXISTS (SELECT 1 FROM dbo.t)"
    assert node.then_branch.kind is K.UPDATE
    assert node.else_branch.kind is K.INSERT


def test_if_with_blocks_and_nested_if(proc):
    p = proc(
        """
        IF @a = 1
        BEGIN
            IF @b = 2 SELECT 1 ELSE SELECT 2
            SELECT 3
        END
        ELSE
            SELECT 4
        SELECT 5
        """,
        params="@a INT, @b INT",
    )
    outer, last = p.body
    assert isinstance(outer, IfNode) and isinstance(outer.then_branch, Block)
    inner = outer.then_branch.body[0]
    assert isinstance(inner, IfNode) and inner.else_branch is not None
    assert isinstance(last, Statement) and last.sql == "SELECT 5"


def test_case_end_inside_a_block_does_not_close_the_block(proc):
    p = proc("IF 1 = 1 BEGIN SELECT CASE WHEN 1 = 1 THEN 1 END AS x SELECT 2 END SELECT 3")
    (if_node, last) = p.body
    assert len(if_node.then_branch.body) == 2
    assert last.sql == "SELECT 3"


def test_while_loop_body(proc):
    p = proc("WHILE @i < 10 BEGIN SET @i = @i + 1 END", params="@i INT")
    (node,) = p.body
    assert isinstance(node, WhileNode)
    assert node.condition.sql == "@i < 10"
    assert [s.kind for s in node.body.body] == [K.SET_VARIABLE]


def test_try_catch_and_transactions(proc):
    p = proc(
        """
        BEGIN TRY
            BEGIN TRANSACTION
            UPDATE dbo.t SET a = 1
            COMMIT TRANSACTION
        END TRY
        BEGIN CATCH
            IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
            THROW;
        END CATCH
        """
    )
    (node,) = p.body
    assert isinstance(node, TryCatchNode)
    assert [s.kind for s in node.try_body] == [K.BEGIN_TRANSACTION, K.UPDATE, K.COMMIT]
    assert isinstance(node.catch_body[0], IfNode)
    assert node.catch_body[1].kind is K.THROW


def test_cursor_statements(proc):
    p = proc(
        """
        DECLARE c CURSOR FAST_FORWARD FOR SELECT id FROM dbo.t
        OPEN c
        FETCH NEXT FROM c INTO @id
        CLOSE c DEALLOCATE c
        """,
        params="@id INT",
    )
    assert kinds(p) == [
        K.DECLARE_CURSOR, K.OPEN_CURSOR, K.FETCH_CURSOR, K.CLOSE_CURSOR, K.DEALLOCATE_CURSOR,
    ]  # fmt: skip


def test_body_without_begin_end(proc):
    from sproc_to_lakehouse.parser import parse_procedures

    (p,) = parse_procedures("CREATE PROCEDURE dbo.p AS SELECT 1 SELECT 2")
    assert len(p.statements()) == 2


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("BEGIN SELECT 1", "unexpected end of input"),
        ("BEGIN TRY SELECT 1 END CATCH", "mismatched END"),
        ("ELSE SELECT 1", "ELSE without IF"),
    ],
)
def test_structural_errors_are_reported(proc, body, message):
    with pytest.raises(ProcedureParseError, match=message):
        proc(body)


def test_statement_text_and_lines_come_from_source(proc):
    p = proc("SELECT 1\n\n  UPDATE dbo.t\n  SET a = 2  -- trailing comment\n")
    first, second = p.statements()
    assert second.sql == "UPDATE dbo.t\n  SET a = 2"
    assert second.line == first.line + 2

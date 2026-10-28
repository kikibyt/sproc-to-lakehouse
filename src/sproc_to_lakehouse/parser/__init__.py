"""Parse T-SQL source files into :class:`~sproc_to_lakehouse.models.Procedure` objects."""

from __future__ import annotations

from pathlib import Path

from sproc_to_lakehouse.models import Procedure
from sproc_to_lakehouse.parser.blocks import parse_body
from sproc_to_lakehouse.parser.header import (
    ProcedureParseError,
    find_procedure_start,
    parse_header,
    split_batches,
)
from sproc_to_lakehouse.parser.lexer import LexError, tokenize

__all__ = ["LexError", "ProcedureParseError", "parse_file", "parse_procedures"]


def parse_procedures(sql: str, source_path: str | None = None) -> list[Procedure]:
    """Parse every ``CREATE PROCEDURE`` batch in ``sql``. Other batches are ignored."""
    procedures: list[Procedure] = []
    for batch in split_batches(tokenize(sql)):
        start = find_procedure_start(batch)
        if start is None:
            continue
        header = parse_header(batch, sql, start)
        body_tokens = batch[header.body_start :]
        if not body_tokens:
            raise ProcedureParseError(f"procedure {header.name} has an empty body", header.line)
        procedures.append(
            Procedure(
                name=header.name,
                schema=header.schema,
                parameters=header.parameters,
                body=parse_body(body_tokens, sql),
                source_sql=sql[batch[start].start : batch[-1].end + 1],
                source_path=source_path,
                line=header.line,
            )
        )
    return procedures


def parse_file(path: str | Path) -> list[Procedure]:
    path = Path(path)
    # utf-8-sig: files saved by SSMS on Windows often carry a BOM.
    return parse_procedures(path.read_text(encoding="utf-8-sig"), source_path=str(path))

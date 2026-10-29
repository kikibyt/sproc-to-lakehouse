"""Deterministic synthetic data generation from a YAML spec and the DDL catalog.

The spec (see ``samples/parity/data.yml``) lists tables in generation order and, per
column, a small generator such as ``{choice: [...]}`` or ``{fk: dbo.patients.patient_id}``.
Values come out as Python types both database drivers accept: ``int``, ``Decimal``,
``str``, ``bool``, ``date``, ``datetime`` or ``None``.
"""

from __future__ import annotations

import itertools
import random
import re
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlglot import exp

from sproc_to_lakehouse.catalog import Catalog, Column, TableSchema

Row = dict[str, Any]
_DIGITS = re.compile(r"\{d(\d)\}")
_MAX_TRIES = 200


class DataSpecError(ValueError):
    pass


@dataclass(frozen=True)
class ColumnType:
    kind: str  # int | decimal | text | date | datetime | bool
    scale: int = 0
    length: int | None = None

    @classmethod
    def of(cls, column: Column) -> ColumnType:
        dtype = exp.DataType.build(column.data_type, dialect="tsql")
        params = [int(p.this.this) for p in dtype.expressions if isinstance(p.this, exp.Literal)]
        if dtype.is_type(exp.DataType.Type.BIT):
            return cls("bool")
        if dtype.is_type(*exp.DataType.INTEGER_TYPES):
            return cls("int")
        if dtype.is_type(exp.DataType.Type.DECIMAL):
            return cls("decimal", scale=params[1] if len(params) > 1 else 0)
        if dtype.is_type(exp.DataType.Type.DATE):
            return cls("date")
        if dtype.is_type(*exp.DataType.TEMPORAL_TYPES):
            return cls("datetime")
        if dtype.is_type(*exp.DataType.TEXT_TYPES):
            return cls("text", length=params[0] if params else None)
        raise DataSpecError(f"unsupported column type {column.data_type} for {column.name}")


def load_spec(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def generate(spec: dict[str, Any], catalog: Catalog) -> dict[str, list[Row]]:
    """Rows per table (``schema.table`` -> list of column dicts), in spec order."""
    seed = int(spec.get("seed", 0))
    data: dict[str, list[Row]] = {}
    for table_name, table_spec in (spec.get("tables") or {}).items():
        schema = catalog.get(table_name)
        if schema is None:
            raise DataSpecError(f"{table_name} is not in the DDL")
        rng = random.Random(seed ^ zlib.crc32(table_name.encode()))
        data[table_name] = _TableGenerator(schema, table_spec or {}, rng, data).rows()
    return data


class _TableGenerator:
    def __init__(self, schema: TableSchema, spec: dict, rng: random.Random, data: dict) -> None:
        self.schema = schema
        self.spec = spec
        self.rng = rng
        self.data = data
        self.columns: dict[str, dict] = spec.get("columns") or {}
        unknown = set(self.columns) - {c.name for c in schema.columns}
        if unknown:
            raise DataSpecError(f"{schema.qualified_name}: unknown columns {sorted(unknown)}")
        self.unique_sets = [tuple(u) for u in spec.get("unique") or []]
        if schema.primary_key:
            self.unique_sets.append(tuple(schema.primary_key))
        self.seen: dict[tuple, set] = {u: set() for u in self.unique_sets}
        self.used_unique_values: dict[str, set] = {}
        self._current: Row = {}

    def rows(self) -> list[Row]:
        cartesian = self.spec.get("cartesian")
        if cartesian:
            names = list(cartesian)
            domains = [self._domain(name, cartesian[name]) for name in names]
            fixed_rows = [
                dict(zip(names, combo, strict=True)) for combo in itertools.product(*domains)
            ]
        else:
            if "rows" not in self.spec:
                raise DataSpecError(f"{self.schema.qualified_name}: give 'rows' or 'cartesian'")
            fixed_rows = [{} for _ in range(int(self.spec["rows"]))]
        return [self._row(n, fixed) for n, fixed in enumerate(fixed_rows, start=1)]

    def _domain(self, name: str, values: Any) -> list[Any]:
        column = self._column(name)
        if isinstance(values, dict) and "fk" in values:
            return self._parent_values(values["fk"], values.get("limit"))
        return [_coerce(v, ColumnType.of(column)) for v in values]

    def _column(self, name: str) -> Column:
        column = self.schema.column(name)
        if column is None:
            raise DataSpecError(f"{self.schema.qualified_name}: unknown column {name}")
        return column

    def _row(self, n: int, fixed: Row) -> Row:
        for _ in range(_MAX_TRIES):
            row = dict(fixed)
            self._current = row
            for column in self.schema.columns:
                if column.name not in row:
                    row[column.name] = self._value(column, self.columns.get(column.name), n)
            keys = {u: tuple(row[c] for c in u) for u in self.unique_sets}
            if all(keys[u] not in self.seen[u] for u in self.unique_sets):
                for u, key in keys.items():
                    self.seen[u].add(key)
                return row
        raise DataSpecError(f"{self.schema.qualified_name}: cannot satisfy unique keys at row {n}")

    def _value(self, column: Column, spec: dict | None, n: int) -> Any:
        ctype = ColumnType.of(column)
        if spec is None:
            return None if column.nullable else _default(ctype, n)
        value = self._from_spec(spec, ctype, n, column)
        if value is None and not column.nullable:
            raise DataSpecError(f"{self.schema.qualified_name}.{column.name} is NOT NULL")
        return value

    def _from_spec(self, spec: dict, ctype: ColumnType, n: int, column: Column) -> Any:
        rng = self.rng
        if "null" in spec and rng.random() < float(spec["null"]):
            return None
        if "mix" in spec:
            options = spec["mix"]
            chosen = rng.choices(options, weights=[o.get("weight", 1) for o in options])[0]
            return self._from_spec(
                {k: v for k, v in chosen.items() if k != "weight"}, ctype, n, column
            )
        if "const" in spec:
            return _coerce(spec["const"], ctype)
        if "seq" in spec:
            return _coerce(int(spec["seq"]) + n - 1, ctype)
        if "template" in spec:
            text = _DIGITS.sub(
                lambda m: "".join(rng.choice("0123456789") for _ in range(int(m.group(1)))),
                spec["template"],
            )
            return _coerce(text.format(n=n), ctype)
        if "choice" in spec:
            return _coerce(rng.choices(spec["choice"], weights=spec.get("weights"))[0], ctype)
        if "int" in spec:
            lo, hi = spec["int"]
            return _coerce(rng.randint(int(lo), int(hi)), ctype)
        if "decimal" in spec:
            lo, hi = (Decimal(str(v)) for v in spec["decimal"])
            raw = lo + (hi - lo) * Decimal(str(rng.random()))
            return _coerce(raw, ctype)
        if "date" in spec:
            return _coerce(_random_date(rng, *spec["date"]), ctype)
        if "datetime" in spec:
            return _coerce(_random_datetime(rng, *spec["datetime"]), ctype)
        if "date_text" in spec:
            options = spec["date_text"]
            day = _random_date(rng, *options["range"])
            return _coerce(day.strftime(rng.choice(options["formats"])), ctype)
        if "bool" in spec:
            return rng.random() < float(spec["bool"])
        if "lookup" in spec:
            return _coerce(self._lookup(spec["lookup"], row_so_far=self._current), ctype)
        if "fk" in spec:
            parents = self._parent_values(spec["fk"], spec.get("limit"))
            if spec.get("unique"):
                used = self.used_unique_values.setdefault(column.name, set())
                remaining = [p for p in parents if p not in used]
                if not remaining:
                    raise DataSpecError(f"{column.name}: not enough parent values for unique fk")
                value = rng.choice(remaining)
                used.add(value)
                return value
            return rng.choice(parents)
        raise DataSpecError(f"{column.name}: no generator in {spec}")

    def _lookup(self, spec: dict, row_so_far: Row) -> Any:
        """Copy a value from the parent row this row references:
        ``{lookup: {by: claim_id, from: dbo.claims.claim_id, take: submitted_at}}``."""
        table, _, key = spec["from"].rpartition(".")
        wanted = row_so_far.get(spec["by"])
        if wanted is None:
            return None
        for parent in self.data.get(table, []):
            if parent[key] == wanted:
                value = parent[spec["take"]]
                return (
                    value.date()
                    if spec.get("as") == "date" and isinstance(value, datetime)
                    else value
                )
        raise DataSpecError(f"lookup: no {table} row with {key} = {wanted!r}")

    def _parent_values(self, reference: str, limit: int | None) -> list[Any]:
        table, _, column = reference.rpartition(".")
        if table not in self.data:
            raise DataSpecError(f"fk {reference}: {table} must be generated earlier in the spec")
        values = [row[column] for row in self.data[table]]
        return values[: int(limit)] if limit else values


def _random_date(rng: random.Random, lo: str, hi: str) -> date:
    start, end = date.fromisoformat(str(lo)), date.fromisoformat(str(hi))
    return start + timedelta(days=rng.randint(0, (end - start).days))


def _random_datetime(rng: random.Random, lo: str, hi: str) -> datetime:
    start, end = datetime.fromisoformat(str(lo)), datetime.fromisoformat(str(hi))
    return start + timedelta(seconds=rng.randint(0, int((end - start).total_seconds())))


def _coerce(value: Any, ctype: ColumnType) -> Any:
    if value is None:
        return None
    if ctype.kind == "int":
        return int(value)
    if ctype.kind == "decimal":
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-ctype.scale), rounding=ROUND_HALF_UP)
    if ctype.kind == "bool":
        return bool(value)
    if ctype.kind == "date":
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if ctype.kind == "datetime":
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    text = str(value)
    if ctype.length is not None and len(text) > ctype.length:
        raise DataSpecError(f"value {text!r} longer than {ctype.length} characters")
    return text


def _default(ctype: ColumnType, n: int) -> Any:
    return {
        "int": n,
        "decimal": Decimal(0).quantize(Decimal(1).scaleb(-ctype.scale)),
        "bool": False,
        "date": date(2024, 1, 1),
        "datetime": datetime(2024, 1, 1),
        "text": f"X{n}"[: ctype.length or None],
    }[ctype.kind]

"""Candidate engine: the generated code running on Apache Spark with Delta Lake.

Steps run exactly as in the generated notebook: the same rendered SQL, the same
``spark.sql(query, args=...)`` calls with string parameter values, scalar loads
collected once, temp views for shared intermediates.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

from sproc_to_lakehouse.catalog import Catalog
from sproc_to_lakehouse.codegen.notebook import scalar_name
from sproc_to_lakehouse.codegen.plan import Plan, StepKind
from sproc_to_lakehouse.codegen.render import SqlRenderer
from sproc_to_lakehouse.parity.compare import RowSet

DELTA_JARS_ENV = "SPROC2LH_DELTA_JARS"


def spark_session(delta_jars: list[str] | None = None):  # pragma: no cover - needs Java
    """Local Spark with Delta Lake. Jars come from ``delta_jars`` (or the
    SPROC2LH_DELTA_JARS environment variable, comma separated) when given, otherwise
    delta-spark downloads them from Maven."""
    from pyspark.sql import SparkSession

    # Timestamps travel between Python and Spark as wall-clock values; pin both to UTC.
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):
        time.tzset()
    jars = delta_jars or [j for j in os.environ.get(DELTA_JARS_ENV, "").split(",") if j]
    builder = (
        SparkSession.builder.master("local[2]")
        .appName("sproc-to-lakehouse-parity")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.warehouse.dir", tempfile.mkdtemp(prefix="sproc2lh-delta-"))
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        )
    )
    if jars:
        session = builder.config("spark.jars", ",".join(jars)).getOrCreate()
    else:
        from delta import configure_spark_with_delta_pip

        session = configure_spark_with_delta_pip(builder).getOrCreate()
    session.sparkContext.setLogLevel("ERROR")
    return session


def _schema_ddl(catalog: Catalog, table: str) -> str:
    """Spark schema for loading Python rows (CHAR/VARCHAR load as STRING)."""
    schema = catalog.get(table)
    parts = []
    for column in schema.columns:
        dtype = column.databricks_type
        if dtype.upper().startswith(("CHAR", "VARCHAR")):
            dtype = "STRING"
        parts.append(f"{column.name} {dtype}")
    return ", ".join(parts)


class SparkEngine:
    name = "Spark + Delta Lake"

    def __init__(self, spark) -> None:
        self.spark = spark
        self.renderer = SqlRenderer(
            table=lambda ref: ref.qualified_name,
            param=lambda name: f":{name}",
            scalar=lambda name: f":{scalar_name(name)}",
        )

    def version(self) -> str:
        import delta  # noqa: F401 - presence check

        return f"Apache Spark {self.spark.version} with Delta Lake"

    def setup(self, catalog: Catalog) -> None:
        for table in catalog._tables.values():
            schema = table.schema or "default"
            self.spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            columns = ", ".join(f"{c.name} {c.databricks_type}" for c in table.columns)
            self.spark.sql(
                f"CREATE OR REPLACE TABLE {table.qualified_name} ({columns}) USING DELTA"
            )

    def load(self, data: dict[str, list[dict[str, Any]]], catalog: Catalog) -> None:
        for table in catalog._tables.values():
            name = table.qualified_name
            self.spark.sql(f"DELETE FROM {name}")
            rows = data.get(name) or []
            if not rows:
                continue
            columns = [c.name for c in table.columns]
            frame = self.spark.createDataFrame(
                [tuple(row[c] for c in columns) for row in rows], schema=_schema_ddl(catalog, name)
            )
            frame.write.insertInto(name)

    def run(self, plan: Plan, params: dict[str, str]) -> list[RowSet]:
        """Run every step of a fully converted plan, as the generated notebook does."""
        if not plan.is_fully_converted:
            raise ValueError(f"{plan.name} has manual steps and cannot run")
        args: dict[str, Any] = {name: params.get(name) for name in plan.env.params}
        results: list[RowSet] = []
        for step in plan.steps:
            if step.kind is StepKind.PRINT:
                continue
            frame = self.spark.sql(self.renderer.render(step.sql), args=args)
            if step.kind is StepKind.SCALAR:
                row = frame.first()
                args[scalar_name(step.name)] = row["value"] if row is not None else None
            elif step.kind is StepKind.TEMP_VIEW:
                frame.createOrReplaceTempView(step.name)
            elif step.kind is StepKind.RESULT:
                results.append(RowSet(frame.columns, [tuple(r) for r in frame.collect()]))
            else:
                frame.collect()
        return results

    def read(self, table: str) -> RowSet:
        frame = self.spark.table(table)
        return RowSet(frame.columns, [tuple(r) for r in frame.collect()])

from __future__ import annotations

from pathlib import Path

import pytest

from sproc_to_lakehouse.models import Procedure
from sproc_to_lakehouse.parser import parse_procedures

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"


def make_procedure(body: str, params: str = "") -> Procedure:
    """Wrap a T-SQL body in CREATE PROCEDURE and parse it."""
    sql = f"CREATE PROCEDURE dbo.usp_test {params}\nAS\nBEGIN\n{body}\nEND\nGO\n"
    procedures = parse_procedures(sql)
    assert len(procedures) == 1
    return procedures[0]


@pytest.fixture
def proc():
    return make_procedure

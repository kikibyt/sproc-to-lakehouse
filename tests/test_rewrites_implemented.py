"""The analyzer may only promise conversions that the generator actually performs."""

from __future__ import annotations

from sproc_to_lakehouse.analyzer import rewrites as rw
from sproc_to_lakehouse.analyzer.rewrites import REWRITES
from sproc_to_lakehouse.codegen import IMPLEMENTED_REWRITES


def test_every_named_rewrite_is_implemented():
    assert set(REWRITES) == IMPLEMENTED_REWRITES


def test_every_rewrite_is_exercised_by_the_corpus(corpus_plans):
    used = set().union(*(plan.rewrites_used() for plan in corpus_plans.values()))
    # plus_to_concat only occurs in statements that stay manual for other reasons
    # (STUFF() in proc 06, dynamic SQL in proc 11); tests/test_sql_rewrites.py covers it.
    assert IMPLEMENTED_REWRITES - used == {rw.PLUS_TO_CONCAT}

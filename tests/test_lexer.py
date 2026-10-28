from __future__ import annotations

import pytest

from sproc_to_lakehouse.parser.lexer import LexError, TokenKind, tokenize


def kinds_and_values(sql: str) -> list[tuple[TokenKind, str]]:
    return [(t.kind, t.value) for t in tokenize(sql)]


def test_merges_variables_system_variables_and_temp_names():
    assert kinds_and_values("SET @rc = @@rowcount FROM #t JOIN ##g") == [
        (TokenKind.WORD, "SET"),
        (TokenKind.VARIABLE, "@rc"),
        (TokenKind.PUNCT, "="),
        (TokenKind.SYSTEM_VARIABLE, "@@ROWCOUNT"),
        (TokenKind.WORD, "FROM"),
        (TokenKind.TEMP_NAME, "#t"),
        (TokenKind.WORD, "JOIN"),
        (TokenKind.TEMP_NAME, "##g"),
    ]


def test_keywords_are_upper_cased_but_quoted_identifiers_are_not_words():
    tokens = tokenize('select [Open], "End" from t')
    assert tokens[0].is_word("SELECT")
    assert tokens[1].kind is TokenKind.QUOTED_IDENTIFIER and tokens[1].value == "Open"
    assert tokens[3].kind is TokenKind.QUOTED_IDENTIFIER and tokens[3].value == "End"


def test_strings_keep_their_source_text():
    (tok,) = tokenize("N'it''s'")
    assert tok.kind is TokenKind.STRING
    assert tok.value == "N'it''s'"


def test_comments_are_dropped_and_offsets_point_into_source():
    sql = "SELECT a -- trailing\n/* block /* nested */ */ FROM t"
    tokens = tokenize(sql)
    assert [t.value for t in tokens] == ["SELECT", "A", "FROM", "T"]
    assert all(sql[t.start : t.end + 1].upper() == t.value for t in tokens)
    assert tokens[2].line == 2


def test_end_and_fetch_do_not_swallow_the_rest_of_the_statement():
    # Regression: sqlglot's tokenizer treats END / FETCH as "commands" and turns the
    # remaining text into one string token, which hid END CATCH and following code.
    values = [t.value for t in tokenize("THROW; END CATCH\nSELECT 1; FETCH NEXT FROM c INTO @a")]
    assert values == [
        "THROW", ";", "END", "CATCH", "SELECT", "1", ";",
        "FETCH", "NEXT", "FROM", "C", "INTO", "@a",
    ]  # fmt: skip


def test_unterminated_string_raises_lex_error():
    with pytest.raises(LexError):
        tokenize("SELECT 'oops")


def test_multi_word_keywords_are_split_into_words():
    # sqlglot emits phrases such as "NONCLUSTERED INDEX" or "GROUP BY" as one token.
    tokens = tokenize("CREATE NONCLUSTERED INDEX ix ON #t (a) GROUP  BY a")
    assert [t.value for t in tokens if t.kind is TokenKind.WORD][:3] == [
        "CREATE",
        "NONCLUSTERED",
        "INDEX",
    ]
    assert [t.value for t in tokens][-3:] == ["GROUP", "BY", "A"]


def test_line_numbers_survive_newlines_inside_keyword_phrases():
    tokens = tokenize("SELECT 1 ORDER\n BY 1\nSELECT @x")
    assert [(t.value, t.line) for t in tokens][-4:] == [
        ("BY", 2),
        ("1", 2),
        ("SELECT", 3),
        ("@x", 3),
    ]

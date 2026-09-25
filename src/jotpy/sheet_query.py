from __future__ import annotations

import re
from dataclasses import dataclass

# Evaluated here. The statement is never handed to sqlite.
ROW_CAP = 2000
_KEYWORDS = frozenset({"SELECT", "FROM", "WHERE", "ORDER", "BY", "LIMIT", "AND", "OR", "ASC", "DESC", "LIKE"})
_COMPARE = frozenset({"=", "<>", "<", "<=", ">", ">="})


class QueryError(Exception):
    def __init__(self, message: str = "Invalid query.") -> None:
        super().__init__(message)
        self.message = message


class TooManyRows(Exception):
    pass


@dataclass
class _Token:
    kind: str
    value: str


@dataclass
class _Query:
    columns: list[str] | None
    where: tuple | None
    order: list[tuple[str, str]]
    limit: int | None


def _ident_char(char: str) -> bool:
    return char == "_" or char.isalpha() or char.isdigit()


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "-" and index + 1 < length and text[index + 1] == "-":
            raise QueryError()
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            raise QueryError()
        if char == ";":
            raise QueryError()
        if char == "*":
            tokens.append(_Token("star", "*"))
            index += 1
            continue
        if char == "(":
            tokens.append(_Token("lparen", "("))
            index += 1
            continue
        if char == ")":
            tokens.append(_Token("rparen", ")"))
            index += 1
            continue
        if char == ",":
            tokens.append(_Token("comma", ","))
            index += 1
            continue
        if char == "<" and index + 1 < length and text[index + 1] in "=>":
            tokens.append(_Token("op", text[index : index + 2]))
            index += 2
            continue
        if char == ">" and index + 1 < length and text[index + 1] == "=":
            tokens.append(_Token("op", ">="))
            index += 2
            continue
        if char in "<>=":
            tokens.append(_Token("op", char))
            index += 1
            continue
        if char in "'\"":
            tokens.append(_read_quoted(text, index, char))
            index += _quoted_length(text, index, char)
            continue
        if char.isdigit() or char.isalpha() or char == "_":
            end = index + 1
            while end < length and _ident_char(text[end]):
                end += 1
            word = text[index:end]
            if word.isdigit():
                tokens.append(_Token("number", word))
            elif word.upper() in _KEYWORDS:
                tokens.append(_Token("keyword", word.upper()))
            else:
                tokens.append(_Token("ident", word))
            index = end
            continue
        raise QueryError()
    return tokens


def _read_quoted(text: str, index: int, quote: str) -> _Token:
    chars: list[str] = []
    cursor = index + 1
    length = len(text)
    while cursor < length:
        if text[cursor] == quote:
            if cursor + 1 < length and text[cursor + 1] == quote:
                chars.append(quote)
                cursor += 2
                continue
            kind = "string" if quote == "'" else "ident"
            return _Token(kind, "".join(chars))
        chars.append(text[cursor])
        cursor += 1
    raise QueryError()


def _quoted_length(text: str, index: int, quote: str) -> int:
    cursor = index + 1
    length = len(text)
    while cursor < length:
        if text[cursor] == quote:
            if cursor + 1 < length and text[cursor + 1] == quote:
                cursor += 2
                continue
            return cursor + 1 - index
        cursor += 1
    raise QueryError()


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> _Token | None:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def accept(self, kind: str, value: str | None = None) -> _Token | None:
        token = self.peek()
        if token is None or token.kind != kind:
            return None
        if value is not None and token.value != value:
            return None
        self.index += 1
        return token

    def parse(self) -> _Query:
        if not self.accept("keyword", "SELECT"):
            raise QueryError()
        columns = self._select_list()
        if self.accept("keyword", "FROM"):
            self._from_name()
        where = None
        if self.accept("keyword", "WHERE"):
            where = self._or_expr()
        order: list[tuple[str, str]] = []
        if self.accept("keyword", "ORDER"):
            if not self.accept("keyword", "BY"):
                raise QueryError()
            order = self._order_list()
        limit = None
        if self.accept("keyword", "LIMIT"):
            number = self.accept("number")
            if number is None:
                raise QueryError()
            limit = int(number.value)
        if self.peek() is not None:
            raise QueryError()
        return _Query(columns, where, order, limit)

    def _select_list(self) -> list[str] | None:
        if self.accept("star"):
            return None
        names = [self._name()]
        while self.accept("comma"):
            names.append(self._name())
        return names

    def _name(self) -> str:
        token = self.peek()
        if token is None or token.kind not in ("ident", "number"):
            raise QueryError()
        self.index += 1
        return token.value

    def _from_name(self) -> None:
        token = self.peek()
        if token is None or token.kind not in ("ident", "number", "keyword", "star", "string"):
            raise QueryError()
        self.index += 1

    def _order_list(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        while True:
            name = self._name()
            direction = "ASC"
            picked = self.accept("keyword", "ASC") or self.accept("keyword", "DESC")
            if picked is not None:
                direction = picked.value
            items.append((name, direction))
            if not self.accept("comma"):
                return items

    def _or_expr(self) -> tuple:
        left = self._and_expr()
        while self.accept("keyword", "OR"):
            left = ("or", left, self._and_expr())
        return left

    def _and_expr(self) -> tuple:
        left = self._primary()
        while self.accept("keyword", "AND"):
            left = ("and", left, self._primary())
        return left

    def _primary(self) -> tuple:
        if self.accept("lparen"):
            expr = self._or_expr()
            if not self.accept("rparen"):
                raise QueryError()
            return expr
        name = self._name()
        if self.accept("keyword", "LIKE"):
            literal = self.accept("string")
            if literal is None:
                raise QueryError()
            return ("like", name, literal.value)
        operator = self.accept("op")
        if operator is None or operator.value not in _COMPARE:
            raise QueryError()
        literal = self.accept("string")
        if literal is None:
            raise QueryError()
        return ("cmp", name, operator.value, literal.value)


def _parse(text: str | None) -> _Query:
    if text is None or not text.strip():
        return _Query(None, None, [], None)
    return _Parser(_tokenize(text)).parse()


def _like(text: str, pattern: str) -> bool:
    pieces: list[str] = []
    for char in pattern:
        if char == "%":
            pieces.append(".*")
        elif char == "_":
            pieces.append(".")
        else:
            pieces.append(re.escape(char))
    return re.fullmatch("".join(pieces), text, flags=re.DOTALL) is not None


def _value(row_id: str, values: dict[str, str], name: str) -> str:
    if name == "_id":
        return row_id
    return values.get(name, "")


def _compare(left: str, operator: str, right: str) -> bool:
    if operator == "=":
        return left == right
    if operator == "<>":
        return left != right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    raise QueryError()


def _eval(expr: tuple, row_id: str, values: dict[str, str]) -> bool:
    kind = expr[0]
    if kind == "or":
        return _eval(expr[1], row_id, values) or _eval(expr[2], row_id, values)
    if kind == "and":
        return _eval(expr[1], row_id, values) and _eval(expr[2], row_id, values)
    if kind == "like":
        return _like(_value(row_id, values, expr[1]), expr[2])
    return _compare(_value(row_id, values, expr[1]), expr[2], expr[3])


def _check_expr(expr: tuple, check) -> None:
    kind = expr[0]
    if kind in ("or", "and"):
        _check_expr(expr[1], check)
        _check_expr(expr[2], check)
        return
    check(expr[1])


def execute(
    column_names: list[str],
    row_ids: list[str],
    row_values: list[dict[str, str]],
    query_text: str | None,
) -> tuple[list[str], list[int], bool]:
    query = _parse(query_text)
    known = set(column_names)

    def check(name: str) -> None:
        if name != "_id" and name not in known:
            raise QueryError("Unknown column.")

    if query.columns is None:
        selected = list(column_names)
    else:
        seen: set[str] = set()
        selected = []
        for name in query.columns:
            check(name)
            if name in seen:
                raise QueryError()
            seen.add(name)
            if name != "_id":
                selected.append(name)
    for name, _direction in query.order:
        check(name)
    if query.where is not None:
        _check_expr(query.where, check)

    if query.where is None and query.limit is None and len(row_ids) > ROW_CAP:
        raise TooManyRows()

    matched: list[int] = []
    for index, row_id in enumerate(row_ids):
        values = row_values[index]
        if query.where is None or _eval(query.where, row_id, values):
            matched.append(index)

    for name, direction in reversed(query.order):
        matched.sort(
            key=lambda index, column=name: _value(row_ids[index], row_values[index], column),
            reverse=direction == "DESC",
        )

    if query.limit is None:
        if len(matched) > ROW_CAP:
            raise TooManyRows()
        return selected, matched, False
    if query.limit > ROW_CAP and len(matched) > ROW_CAP:
        raise TooManyRows()
    cap = min(query.limit, ROW_CAP)
    chosen = matched[:cap]
    return selected, chosen, len(matched) > len(chosen)

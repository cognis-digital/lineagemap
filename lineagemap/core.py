"""Core lineage engine. Standard library only.

Approach (intentionally pragmatic, not a full SQL parser):
  1. Strip comments and dbt Jinja, rewriting {{ ref('m') }} -> m and
     {{ source('s','t') }} -> s__t so they look like plain table names.
  2. Split a query into its FROM/JOIN sources (with aliases) and the
     comma-separated SELECT expressions.
  3. For each SELECT expression, find its output name (explicit AS or the
     trailing identifier) and the set of source columns it references,
     resolving alias.col -> realtable.col using the FROM/JOIN map.

This covers the common analyst cases: aliased joins, qualified columns,
`SELECT *`, expressions over multiple columns, and dbt ref/source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Tuple

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_REF = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_SOURCE = re.compile(r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_QUALIFIED = re.compile(rf"({_IDENT})\.({_IDENT})")
_BARE_COL = re.compile(rf"\b({_IDENT})\b")

_KEYWORDS = {
    "select", "from", "where", "and", "or", "not", "as", "on", "join",
    "left", "right", "inner", "outer", "full", "cross", "using", "group",
    "by", "order", "having", "limit", "distinct", "case", "when", "then",
    "else", "end", "is", "null", "in", "like", "between", "asc", "desc",
    "with", "union", "all", "over", "partition", "true", "false", "cast",
}
_FUNCS = {
    "sum", "count", "avg", "min", "max", "coalesce", "round", "abs",
    "lower", "upper", "trim", "concat", "length", "cast", "date", "now",
    "row_number", "rank", "lag", "lead", "nullif", "greatest", "least",
    "extract", "date_trunc", "floor", "ceil",
}


@dataclass
class ColumnLineage:
    """Lineage for a single output column."""
    output: str
    sources: List[str] = field(default_factory=list)  # "table.column"
    expression: str = ""
    is_star: bool = False


@dataclass
class Lineage:
    """Lineage for one query / model."""
    model: str
    upstream_tables: List[str] = field(default_factory=list)
    columns: List[ColumnLineage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _preprocess(sql: str) -> str:
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    sql = _SOURCE.sub(lambda m: f"{m.group(1)}__{m.group(2)}", sql)
    sql = _REF.sub(lambda m: m.group(1), sql)
    sql = _JINJA.sub(" ", sql)  # drop config()/other jinja
    return sql


def _split_top_level(text: str, sep: str = ",") -> List[str]:
    """Split on `sep` ignoring anything inside parentheses."""
    parts, depth, buf = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _find_clause(sql: str) -> Tuple[str, str]:
    """Return (select_body, from_body) for the outermost SELECT ... FROM ..."""
    low = sql.lower()
    si = low.find("select")
    if si == -1:
        return "", ""
    # find matching FROM at depth 0 after select
    depth = 0
    fi = -1
    i = si + len("select")
    while i < len(sql):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and low[i:i + 4] == "from" and _is_word(low, i, 4):
            fi = i
            break
        i += 1
    if fi == -1:
        return sql[si + 6:].strip(), ""
    select_body = sql[si + 6:fi]
    # from-body ends at next top-level clause keyword
    rest = sql[fi + 4:]
    from_body = _cut_at_clause(rest)
    return select_body.strip(), from_body.strip()


def _is_word(text: str, idx: int, length: int) -> bool:
    before = text[idx - 1] if idx > 0 else " "
    after = text[idx + length] if idx + length < len(text) else " "
    return not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_")


_CLAUSE_END = re.compile(r"\b(where|group\s+by|order\s+by|having|limit|union|window|qualify)\b", re.IGNORECASE)


def _cut_at_clause(text: str) -> str:
    depth = 0
    for m in _CLAUSE_END.finditer(text):
        # count parens before match
        seg = text[:m.start()]
        if seg.count("(") - seg.count(")") == 0:
            return text[:m.start()]
    return text


def _parse_sources(from_body: str) -> Tuple[Dict[str, str], List[str]]:
    """Parse FROM/JOIN clause. Return (alias->table, ordered tables)."""
    # normalize JOIN ... ON ... into separate source tokens
    body = re.sub(r"\bon\b.*?(?=\bjoin\b|$)", " ", from_body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"\b(left|right|inner|outer|full|cross)\b", " ", body, flags=re.IGNORECASE)
    body = re.sub(r"\bjoin\b", ",", body, flags=re.IGNORECASE)
    body = re.sub(r"\busing\b.*", " ", body, flags=re.IGNORECASE | re.DOTALL)
    alias_map: Dict[str, str] = {}
    tables: List[str] = []
    for token in _split_top_level(body, ","):
        token = token.strip()
        if not token or token.startswith("("):
            continue
        words = [w for w in re.split(r"\s+|\bas\b", token, flags=re.IGNORECASE) if w]
        if not words:
            continue
        table = words[0]
        alias = words[-1] if len(words) > 1 else table
        if not re.match(rf"^{_IDENT}$", table):
            continue
        alias_map[alias] = table
        alias_map.setdefault(table, table)
        if table not in tables:
            tables.append(table)
    return alias_map, tables


def _resolve_columns(expr: str, alias_map: Dict[str, str], tables: List[str]) -> List[str]:
    """Find source columns referenced in an expression."""
    sources: List[str] = []
    consumed: set = set()
    for m in _QUALIFIED.finditer(expr):
        alias, col = m.group(1), m.group(2)
        table = alias_map.get(alias, alias)
        ref = f"{table}.{col}"
        if ref not in sources:
            sources.append(ref)
        consumed.add(m.group(0))
    # bare columns: attribute to single source if unambiguous
    masked = _QUALIFIED.sub(" ", expr)
    for m in _BARE_COL.finditer(masked):
        word = m.group(1)
        low = word.lower()
        if low in _KEYWORDS or low in _FUNCS:
            continue
        if word in alias_map:  # it's a table/alias name, not a column
            continue
        # skip numeric literals / function-call names (followed by '(')
        after = masked[m.end():].lstrip()
        if after.startswith("("):
            continue
        if len(tables) == 1:
            ref = f"{tables[0]}.{word}"
        else:
            ref = f"?.{word}"
        if ref not in sources:
            sources.append(ref)
    return sources


def _output_name(expr: str) -> Tuple[str, str]:
    """Return (output_name, core_expr_without_alias)."""
    # explicit AS
    m = re.search(rf"\bas\s+([\"`]?)({_IDENT})\1\s*$", expr, re.IGNORECASE)
    if m:
        return m.group(2), expr[:m.start()].strip()
    # implicit alias: `expr name` where name is a trailing bare identifier
    m = re.search(rf"([\"`]?)({_IDENT})\1\s*$", expr)
    if m:
        tail = m.group(2)
        head = expr[:m.start()].strip()
        # only treat as alias if there is a preceding expression token
        if head and not head.endswith(".") and tail.lower() not in _KEYWORDS:
            # qualified col like a.b -> output is the column part
            return tail, expr
    # qualified column with no alias -> output is the column
    qm = _QUALIFIED.search(expr.strip())
    if qm and qm.group(0) == expr.strip():
        return qm.group(2), expr
    return expr.strip(), expr


def extract_lineage(sql: str, model: str = "query") -> Lineage:
    """Extract column lineage from a single SQL statement."""
    clean = _preprocess(sql)
    select_body, from_body = _find_clause(clean)
    alias_map, tables = _parse_sources(from_body)
    lineage = Lineage(model=model, upstream_tables=tables)
    if not select_body:
        return lineage
    for expr in _split_top_level(select_body, ","):
        expr = expr.strip()
        if not expr:
            continue
        if expr == "*" or re.match(rf"^{_IDENT}\.\*$", expr):
            srcs = [f"{t}.*" for t in tables] if expr == "*" else [
                f"{alias_map.get(expr.split('.')[0], expr.split('.')[0])}.*"
            ]
            lineage.columns.append(
                ColumnLineage(output="*", sources=srcs, expression=expr, is_star=True)
            )
            continue
        name, core = _output_name(expr)
        sources = _resolve_columns(core, alias_map, tables)
        lineage.columns.append(
            ColumnLineage(output=name, sources=sources, expression=expr)
        )
    return lineage


def _model_name(path: str) -> str:
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return base[:-4] if base.lower().endswith(".sql") else base


def analyze_files(paths: List[str]) -> List[Lineage]:
    """Read each .sql file and extract lineage. Raises on read failure."""
    results = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as fh:
            sql = fh.read()
        results.append(extract_lineage(sql, model=_model_name(p)))
    return results


def build_dbt_graph(lineages: List[Lineage]) -> Dict[str, List[str]]:
    """Return model -> [upstream models] edges, keeping only known models."""
    names = {lin.model for lin in lineages}
    graph: Dict[str, List[str]] = {}
    for lin in lineages:
        ups = [t for t in lin.upstream_tables if t in names]
        graph[lin.model] = sorted(set(ups))
    return graph

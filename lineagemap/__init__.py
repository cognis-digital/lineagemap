"""LINEAGEMAP - Column-level lineage extracted from SQL and dbt.

Standard-library-only engine that parses SQL SELECT statements (and dbt
models using {{ ref('...') }} / {{ source('...') }}) and resolves which
upstream table.columns feed each output column.
"""
from .core import (
    Lineage,
    ColumnLineage,
    extract_lineage,
    analyze_files,
    build_dbt_graph,
)

TOOL_NAME = "lineagemap"
TOOL_VERSION = "1.0.0"

__all__ = [
    "Lineage",
    "ColumnLineage",
    "extract_lineage",
    "analyze_files",
    "build_dbt_graph",
    "TOOL_NAME",
    "TOOL_VERSION",
]

"""Private stdio source reader: trusted launcher supplies root and tracked inventory."""
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from admin_ops.code_read import CodeReader

mcp = FastMCP("ocr_code")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
reader = None


def call(name: str, **arguments) -> dict:
    try:
        return {"status": "ok", **getattr(reader, name)(**arguments)}
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "unavailable", "reason": type(exc).__name__,
                "note": "Only allowlisted tracked source files in this clone can be read; check path and bounds."}


@mcp.tool(annotations=READ_ONLY)
def list_code_files(path_contains: str = "", offset: int = 0, limit: int = 100) -> dict:
    """List allowed tracked source paths (max 100 per page); optional literal path substring filter."""
    return call("list_code_files", path_contains=path_contains, offset=offset, limit=limit)


@mcp.tool(annotations=READ_ONLY)
def search_code(query: str, path_contains: str = "", limit: int = 40) -> dict:
    """Find literal text in allowed source files; return paths, line numbers and snippets. No regex or shell. Max 60 matches; narrow path_contains if truncated."""
    return call("search_code", query=query, path_contains=path_contains, limit=limit)


@mcp.tool(annotations=READ_ONLY)
def read_code_file(path: str, start_line: int = 1, line_count: int = 120) -> dict:
    """Read up to 200 numbered lines of an exact repository-relative source path returned by search/list. No absolute paths, traversal, symlinks, secrets or data files."""
    return call("read_code_file", path=path, start_line=start_line, line_count=line_count)


if __name__ == "__main__":
    with Path(os.environ["OCR_CODE_MANIFEST"]).open("rb") as manifest:
        raw = manifest.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("source inventory too large")
    reader = CodeReader(Path(os.environ["OCR_CODE_ROOT"]), json.loads(raw))
    mcp.run(transport="stdio")

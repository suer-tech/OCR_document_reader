"""Private stdio source tools; only the trusted HH fixer launcher enables edits."""
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from admin_ops.code_read import CodeReader
from admin_ops.code_write import CodeWriter

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def create_server(root: Path, tracked_paths: list[str], target: str = "ocr", *, writable: bool = False) -> FastMCP:
    if writable and target != "hh":
        raise ValueError("Bounded writes require the trusted HH target")
    reader = CodeReader(root, tracked_paths, target)
    writer = CodeWriter(root, tracked_paths) if writable else None
    server = FastMCP("ocr_code")

    def call(name: str, **arguments) -> dict:
        try:
            return {"status": "ok", **getattr(reader, name)(**arguments)}
        except (OSError, ValueError, TypeError, UnicodeError):
            return {"status": "unavailable", "note": "Only allowlisted source paths and bounded reads are available."}

    @server.tool(annotations=READ_ONLY)
    def list_code_files(path_contains: str = "", offset: int = 0, limit: int = 100) -> dict:
        """List allowlisted source paths, at most 100 per page. Optional literal substring filter."""
        return call("list_code_files", path_contains=path_contains, offset=offset, limit=limit)

    @server.tool(annotations=READ_ONLY)
    def search_code(query: str, path_contains: str = "", limit: int = 40) -> dict:
        """Find literal text in allowlisted source files; return paths, line numbers and snippets. No regex or shell."""
        return call("search_code", query=query, path_contains=path_contains, limit=limit)

    @server.tool(annotations=READ_ONLY)
    def read_code_file(path: str, start_line: int = 1, line_count: int = 120) -> dict:
        """Read up to 200 numbered lines of a source path. No absolute paths, traversal, symlinks, credentials or data."""
        return call("read_code_file", path=path, start_line=start_line, line_count=line_count)

    if writer is not None:
        def edit(name: str, **arguments) -> dict:
            try:
                result = getattr(writer, name)(**arguments)
                path = result["path"]
                reader.allowed.add(path)
                reader.paths = sorted(reader.allowed)
                return {"status": "ok", **result, "note": "Isolated source proposal only; tests have not run and nothing is published."}
            except (OSError, ValueError, TypeError, UnicodeError):
                return {"status": "unavailable", "note": "HH source only: five files, 500 kB per file, no symlinks, credentials or data; exact replacement must match once."}

        @server.tool(annotations=WRITE)
        def write_code_file(path: str, content: str) -> dict:
            """Create or replace one complete UTF-8 HH source/test file in the disposable clone. Maximum five files, 500 kB each; no secrets, data, deployment or deletion."""
            return edit("write_code_file", path=path, content=content)

        @server.tool(annotations=WRITE)
        def replace_code_text(path: str, old_text: str, new_text: str) -> dict:
            """Replace one exact unique text block in an existing HH source file, preserving the rest. Include enough old_text context to match exactly once. Never runs code or commands."""
            return edit("replace_code_text", path=path, old_text=old_text, new_text=new_text)

    return server


if __name__ == "__main__":
    with Path(os.environ["OCR_CODE_MANIFEST"]).open("rb") as manifest:
        raw = manifest.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("source inventory too large")
    create_server(Path(os.environ["OCR_CODE_ROOT"]), json.loads(raw), os.environ.get("OPS_CODE_TARGET", "ocr"),
                  writable=os.environ.get("OPS_CODE_WRITE") == "true").run(transport="stdio")

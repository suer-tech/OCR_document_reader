"""Bounded HH source edits in a disposable clone, without model shell access."""
import json
import os
import tempfile
import threading
from pathlib import Path, PurePosixPath

from admin_ops.paths import validate_change_path

WRITE_TOOLS = ["write_code_file", "replace_code_text"]
MAX_FILE_BYTES = 500_000


class CodeWriter:
    def __init__(self, root: Path, tracked_paths: list[str]):
        if root.is_symlink():
            raise ValueError("source root must not be a symlink")
        self.root = root.resolve(strict=True)
        if not (self.root / ".git").is_dir() or (self.root / ".git").is_symlink():
            raise ValueError("an isolated Git clone is required")
        if len(tracked_paths) > 10000:
            raise ValueError("source inventory limit exceeded")
        self.tracked = set(tracked_paths)
        self.written: set[str] = set()
        self.lock = threading.RLock()

    def _path(self, path: str) -> Path:
        if (not isinstance(path, str) or len(path) > 500
                or PurePosixPath(path).as_posix() != path
                or not validate_change_path(path, "hh")):
            raise ValueError("path is outside the HH source edit policy")
        target = self.root
        for part in PurePosixPath(path).parts:
            target = target / part
            if target.is_symlink():
                raise ValueError("symlinks cannot be edited")
        if not target.resolve().is_relative_to(self.root):
            raise ValueError("path escapes the source clone")
        if target.exists() and (not target.is_file() or path not in self.tracked | self.written):
            raise ValueError("existing file is outside the tracked inventory")
        return target

    @staticmethod
    def _content(path: str, content: str) -> bytes:
        if not isinstance(content, str):
            raise TypeError("content must be UTF-8 text")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES or b"\0" in data:
            raise ValueError("content is too large or binary")
        if path.endswith(".json"):
            def invalid_constant(value: str):
                raise ValueError("JSON cannot contain nonfinite values")
            json.loads(content, parse_constant=invalid_constant)
        return data

    def _read(self, path: str, target: Path) -> str:
        with target.open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES or b"\0" in data:
            raise ValueError("existing file is too large or binary")
        return data.decode("utf-8")

    def write_code_file(self, path: str, content: str) -> dict:
        with self.lock:
            target = self._path(path)
            data = self._content(path, content)
            if target.exists() and self._read(path, target) == content:
                return {"path": path, "changed": False}
            if path not in self.written and len(self.written) >= 5:
                raise ValueError("a proposal may edit at most five files")
            mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(data)
                temporary.chmod(mode)
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self.written.add(path)
            return {"path": path, "changed": True, "bytes": len(data)}

    def replace_code_text(self, path: str, old_text: str, new_text: str) -> dict:
        with self.lock:
            target = self._path(path)
            if (not isinstance(old_text, str) or not old_text
                    or not isinstance(new_text, str) or not target.is_file()):
                raise ValueError("an existing file and nonempty exact match are required")
            content = self._read(path, target)
            if content.count(old_text) != 1:
                raise ValueError("old_text must match exactly once; read a larger context")
            return self.write_code_file(path, content.replace(old_text, new_text, 1))

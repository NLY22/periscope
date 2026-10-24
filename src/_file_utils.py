"""Internal file-system utilities."""

import os
import tempfile
from pathlib import Path

from periscope.runtime.files import replace_file


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text via a same-directory temporary file and atomic replacement."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
        replace_file(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

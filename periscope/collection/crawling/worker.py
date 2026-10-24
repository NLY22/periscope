"""Internal parser worker: JSON header plus raw bytes in, one JSON document out."""
from dataclasses import asdict
import json
import sys

from .documents import extract_document


def main() -> int:
    try:
        header = json.loads(sys.stdin.buffer.readline(65536))
        body = sys.stdin.buffer.read(20 * 1024 * 1024 + 1)
        if len(body) > 20 * 1024 * 1024:
            raise ValueError("document_input_too_large")
        payload = asdict(extract_document(body, header["content_type"], header["url"]))
        code = 0
    except Exception as exc:
        payload = {"error": str(exc)[:150] if isinstance(exc, ValueError) else type(exc).__name__}
        code = 1
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

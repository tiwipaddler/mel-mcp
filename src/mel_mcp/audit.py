"""Audit log: one JSON line per tool call in logs/audit.jsonl.

Only the size of each result is logged, so the log never becomes a second copy of the data.
"""

import json
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path, user: str):
        self.path = Path(path)
        self.user = user

    def write(self, entry: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            # Never break a tool call because logging failed; stderr is safe in MCP.
            print(f"audit log write failed: {exc}", file=sys.stderr)

    @contextmanager
    def record(self, tool: str, arguments: dict):
        """Wrap a tool call. Yields a dict where the caller can note the result size."""
        started = time.perf_counter()
        outcome = {"results": None}
        entry = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "user": self.user,
            "tool": tool,
            "arguments": {k: v for k, v in arguments.items() if v is not None},
        }
        try:
            yield outcome
            entry["ok"] = True
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)[:500]
            raise
        finally:
            entry["results"] = outcome["results"]
            entry["duration_ms"] = round((time.perf_counter() - started) * 1000)
            self.write(entry)


def result_size(result) -> int | None:
    """How many items a tool returned, for the log."""
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        for key in ("results", "findings", "periods", "indicators"):
            if isinstance(result.get(key), list):
                return len(result[key])
        return 1
    return None

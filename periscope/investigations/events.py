"""Progress messages emitted alongside saved evidence batches."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceProgress:
    source: str
    status: str
    reason: str = ""

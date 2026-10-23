"""Atomic JSON writes: a reader never sees a half-written file."""
import json
import os


def write_json(path, obj, **dump_kw):
    """Write `obj` to `path` via a per-process temp file and os.replace.
    Errors propagate; each caller decides what a failed save means."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, **dump_kw)
    os.replace(tmp, path)

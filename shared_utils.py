"""Utilitários partilhados: bloqueio HID entre processos, escrita atómica JSON e deteção de input."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import glob
import json
import os
import tempfile
import threading
from typing import Iterator

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_LOCAL_LOCK = threading.RLock()
_LOCK_DEPTH = 0
_LOCK_FD: int | None = None


def _lock_file_path() -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return os.path.join(runtime_dir, "perifericos-rgb-hid.lock")
    return f"/tmp/perifericos-rgb-hid-{os.getuid()}.lock"


@contextmanager
def hid_lock() -> Iterator[None]:
    """Serializa acessos a /dev/hidraw* entre threads do mesmo processo e entre processos."""
    global _LOCK_DEPTH, _LOCK_FD
    with _LOCAL_LOCK:
        if _LOCK_DEPTH == 0:
            fd = os.open(_lock_file_path(), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except Exception:
                os.close(fd)
                raise
            _LOCK_FD = fd
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
            if _LOCK_DEPTH == 0 and _LOCK_FD is not None:
                fd_to_close = _LOCK_FD
                _LOCK_FD = None
                try:
                    fcntl.flock(fd_to_close, fcntl.LOCK_UN)
                finally:
                    os.close(fd_to_close)


def atomic_write_json(path: str, data: object, indent: int = 2, ensure_ascii: bool = True) -> None:
    """Escreve JSON de forma atómica (ficheiro temporário no mesmo diretório + os.replace)."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def find_input_events(vid: int, pids: tuple[int, ...], require_rel_xy: bool = False) -> list[str]:
    """Devolve /dev/input/event* legíveis para o VID e PIDs indicados."""
    target_vid = f"{vid:04x}"
    target_pids = {f"{p:04x}" for p in pids}
    paths: list[str] = []
    for sys_path in sorted(glob.glob("/sys/class/input/event*")):
        try:
            with open(os.path.join(sys_path, "device", "id", "vendor"), "r", encoding="utf-8") as f:
                ev_vid = f.read().strip().lower()
            with open(os.path.join(sys_path, "device", "id", "product"), "r", encoding="utf-8") as f:
                ev_pid = f.read().strip().lower()
            if ev_vid != target_vid or ev_pid not in target_pids:
                continue
            if require_rel_xy:
                rel_path = os.path.join(sys_path, "device", "capabilities", "rel")
                with open(rel_path, "r", encoding="utf-8") as f:
                    rel_caps = int(f.read().strip().split()[-1], 16)
                if (rel_caps & 0x3) != 0x3:
                    continue
            ev_path = f"/dev/input/{os.path.basename(sys_path)}"
            if os.access(ev_path, os.R_OK):
                paths.append(ev_path)
        except (OSError, ValueError, IndexError):
            continue
    return paths

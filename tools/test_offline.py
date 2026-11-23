#!/usr/bin/env python3
"""Run tests with credentials removed and network access limited to local test servers."""
from __future__ import annotations
import ipaddress
import os
from pathlib import Path
import socket
import sys

# Bound verification resource usage, including collection of legacy test fixtures.
try:
    import resource
    resource.setrlimit(resource.RLIMIT_DATA, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
except (ImportError, ValueError, OSError):
    pass

ROOT = Path(__file__).resolve().parents[1]
for name in list(os.environ):
    if any(word in name.upper() for word in ("API_KEY", "PRIVATE_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "WEBHOOK")) or name == "CONFIRM_LIVE_TRADING":
        os.environ.pop(name, None)
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["PYTHONUNBUFFERED"] = "1"

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo

def _allowed(host: object) -> bool:
    if host is None or host in ("localhost", "", b"localhost"):
        return True
    try:
        return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
    except (ValueError, TypeError):
        return False

def _check(address: object) -> None:
    if isinstance(address, tuple):
        if not _allowed(address[0]) or address[1] in (6500, 9280):
            raise OSError("Offline verification blocks external services and CAD endpoints")

def _connect(self: socket.socket, address: object):
    _check(address)
    return _original_connect(self, address)

def _connect_ex(self: socket.socket, address: object):
    _check(address)
    return _original_connect_ex(self, address)

def _getaddrinfo(host, port, *args, **kwargs):
    if not _allowed(host):
        raise OSError("Offline verification blocks external DNS lookups")
    return _original_getaddrinfo(host, port, *args, **kwargs)

socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.getaddrinfo = _getaddrinfo

import unittest
component = sys.argv[1] if len(sys.argv) > 1 else "geometry-service"
if component not in ("geometry-service", "artisan-mvp"):
    raise SystemExit("Choose geometry-service or artisan-mvp")
root = ROOT / component
os.chdir(root)
sys.path.insert(0, str(root))
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())

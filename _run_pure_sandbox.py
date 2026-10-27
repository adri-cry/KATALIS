"""Run test_pure.py with workspace-writable temp directories.
The DSH sandbox denies writes to Python tempfile's Windows 0700 directories.
"""
import os
import runpy
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(ROOT, "data", "cache")


def mkdtemp(suffix=None, prefix=None, dir=None, **kwargs):
    parent = dir or BASE
    os.makedirs(parent, exist_ok=True)
    while True:
        path = os.path.join(parent, (prefix or "tmp") + uuid.uuid4().hex[:10] + (suffix or ""))
        try:
            os.makedirs(path, exist_ok=False)
            return path
        except FileExistsError:
            pass


tempfile.mkdtemp = mkdtemp
sys.path.insert(0, ROOT)
sys.argv = [os.path.join(ROOT, "tools", "test_pure.py")]
runpy.run_path(sys.argv[0], run_name="__main__")

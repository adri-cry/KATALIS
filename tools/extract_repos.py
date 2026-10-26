"""Extract the archives in ``vendor/`` into the layout KATALIS expects.

The two upstream repositories and the prebuilt ``idx`` binary are downloaded by
the Node scripts (git cannot reach GitHub from every machine), then unpacked
here. Only the standard library is used.

    node tools/fetch_repos.mjs            # sumber kedua repo
    node tools/fetch_idx_binary.mjs       # biner idx siap pakai (opsional)
    python tools/extract_repos.py         # kedua-duanya ke vendor/
"""

from __future__ import annotations

import shutil
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

# archive name -> folder under vendor/
_JOBS = {
    "TradingAgents.zip": "tradingagents",
    "idx.zip": "idx",
}
# A release archive names the platform in its file name and carries the binary.
_BINARY_HINTS = ("windows", "linux", "darwin")


def extract(archive_path: Path, destination: Path) -> int:
    """Unpack one zip, dropping its single top-level directory if it has one."""
    with zipfile.ZipFile(archive_path) as archive:
        names = [name for name in archive.namelist() if name and not name.endswith("/")]
        if not names:
            return 0
        roots = {name.split("/", 1)[0] for name in names}
        prefix = f"{roots.pop()}/" if len(roots) == 1 else ""
        for name in names:
            relative = name[len(prefix):] if prefix and name.startswith(prefix) else name
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink)
            if target.suffix in (".exe", "") and "bin" in target.parts:
                # A copied binary loses its execute bit on POSIX systems.
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return len(names)


def main() -> int:
    if not VENDOR.is_dir():
        print("vendor/ belum ada — jalankan dulu: node tools/fetch_repos.mjs", file=sys.stderr)
        return 1

    archives = sorted(VENDOR.glob("*.zip"))
    if not archives:
        print("tidak ada arsip di vendor/ — jalankan dulu: node tools/fetch_repos.mjs",
              file=sys.stderr)
        return 1

    total = 0
    for archive in archives:
        name = archive.name
        if name in _JOBS:
            destination = VENDOR / _JOBS[name]
        elif any(hint in name.lower() for hint in _BINARY_HINTS):
            destination = VENDOR / "idx" / "bin"      # prebuilt idx binary
        else:
            print(f"lewati {name} (tidak dikenali)")
            continue
        count = extract(archive, destination)
        print(f"{name} -> {destination.relative_to(ROOT)}  ({count} berkas)")
        total += count

    if not total:
        return 1
    print("selesai")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

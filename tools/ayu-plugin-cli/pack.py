#!/usr/bin/env python3
"""Pack plugin dir to .ayupack (zip, no ZipSlip): python pack.py <dir> <out>."""
import sys
import zipfile
from pathlib import Path


def main() -> int:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            rel = p.relative_to(src).as_posix()
            assert ".." not in rel and not rel.startswith("/"), rel
            z.write(p, rel)
    print(f"packed {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

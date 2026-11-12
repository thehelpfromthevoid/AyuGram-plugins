"""Give every shim android.* module a __path__ so submodules can resolve.

`android.graphics` was a plain .py file, so Python refused to descend into it:
`import android.graphics.drawable` failed with "'android.graphics' is not a
package" *before* any meta path finder was consulted, because that check looks
only at whether the parent module has __path__.

The stub finder already answers any android.* name that has no real module, so
declaring __path__ = [] is all it takes for the whole android namespace to
become as deep as a plugin expects.
"""
from __future__ import annotations

import sys
from pathlib import Path

BANNER = (
    "\n\n# Added by tools: this shim module doubles as a package so that\n"
    "# `android.{name}.<anything>` resolves. Without __path__ Python treats\n"
    "# it as a plain module and raises \"is not a package\" before any\n"
    "# meta path finder is consulted.\n__path__ = []\n"
)


def main() -> int:
	root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
	changed = 0
	for path in sorted(root.glob("*.py")):
		if path.name == "__init__.py":
			continue
		text = path.read_text(encoding="utf-8-sig")
		if "\n__path__" in text or text.rstrip().endswith("__path__ = []"):
			continue
		body = text.rstrip("\n")
		path.write_text(
			body + BANNER.format(name=path.stem),
			encoding="utf-8",
		)
		print(f"  __path__ added: {path.name}")
		changed += 1
	print(f"{changed} module(s) updated")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
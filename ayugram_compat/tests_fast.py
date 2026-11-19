"""Cross-checks the native fast path against the pure Python one.

Run: python ayugram_compat/tests_fast.py
Exits non-zero on any mismatch, so it can gate a build.
"""
from __future__ import annotations

import ctypes  # noqa: F401  # imported before the repo root joins sys.path
import json
import random
import string
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ayugram_compat import fast  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
	if condition:
		print(f"ok   {name}")
	else:
		FAILURES.append(f"{name}{': ' + detail if detail else ''}")
		print(f"FAIL {name} {detail}")


def naive_replace(text: str, pairs: list[tuple[str, str]]) -> tuple[str, bool]:
	"""Sequential str.replace, what the wordfilter plugin does today."""
	out = text
	for needle, replacement in pairs:
		if needle:
			out = out.replace(needle, replacement)
	return out, out != text


def main() -> int:
	print(f"native available: {fast.available()}")
	if not fast.available():
		print(f"load error: {fast.load_error()}")
		return 2

	# --- filter semantics: the native engine is the replace loop ------------
	cases = [
		([("brb", "be right back"), ("imo", "in my opinion")], "brb, imo, ok"),
		([("a", "1"), ("aa", "2")], "aaa a"),
		([("привет", "здравствуйте")], "привет, мир"),
		([("🙂", ":smile:")], "hi 🙂 there"),
		([("x", "")], "x y x"),
		([("", "ignored"), ("k", "v")], "k"),
		# the cases a single leftmost-longest pass gets wrong
		([("ab", "c"), ("cd", "X")], "abd"),
		([("a", ""), ("bb", "b")], "bab"),
		([("a", ""), ("bb", "")], "bab"),
		([("aa", ""), ("ba", "")], "baa"),
	]
	for index, (pairs, text) in enumerate(cases):
		with fast.WordFilter(pairs) as f:
			got, changed = f.replace(text)
			sequential, sequential_changed = naive_replace(text, pairs)
			check(f"filter case {index}", (got, changed) == (sequential, sequential_changed),
			      f"{got!r}/{changed} vs {sequential!r}/{sequential_changed}")

	# --- a pair set that cascades is answered exactly like str.replace
	cascading = [("a", "b"), ("b", "c")]
	with fast.WordFilter(cascading) as f:
		got, changed = f.replace("a b")
		sequential, sequential_changed = naive_replace("a b", cascading)
		check("cascading stays sequential", (got, changed) == (sequential, sequential_changed),
		      f"{got!r}/{changed} vs {sequential!r}/{sequential_changed}")

	# --- needles that overlap each other are still the loop's answer
	overlapping = [("a", "1"), ("aa", "2")]
	with fast.WordFilter(overlapping) as f:
		got, _ = f.replace("aaa")
		sequential, _ = naive_replace("aaa", overlapping)
		check("overlapping matches the loop", got == sequential, f"{got!r} vs {sequential!r}")

	# --- and so is a set that once looked "clean"
	clean = [("aa", "1"), ("b", "2")]
	with fast.WordFilter(clean) as f:
		got, _ = f.replace("aaa b")
		sequential, _ = naive_replace("aaa b", clean)
		check("clean set matches the loop", got == sequential, f"{got!r} vs {sequential!r}")

	# --- filter matches the sequential replace when needles do not overlap
	pairs = [("brb", "be right back"), ("imo", "in my opinion"), ("thx", "thanks")]
	with fast.WordFilter(pairs) as f:
		for text in ["brb", "imo thx", "nothing here", "brbimo", "", "  "]:
			sequential, seq_changed = naive_replace(text, pairs)
			native, native_changed = f.replace(text)
			check(f"filter parity {text!r}", (native, native_changed) == (sequential, seq_changed),
			      f"{native!r}/{native_changed} vs {sequential!r}/{seq_changed}")

	# --- randomized parity against a long random text
	random.seed(7)
	alphabet = string.ascii_letters + "   ,.!прив"
	words = ["brb", "imo", "thx", "ok", "прив"]
	random_pairs = [(word, f"<{word}>") for word in words]
	with fast.WordFilter(random_pairs) as f:
		for _ in range(200):
			text = "".join(random.choice(alphabet) for _ in range(random.randint(0, 60)))
			sequential, _ = naive_replace(text, random_pairs)
			native, _ = f.replace(text)
			if native != sequential:
				check("random parity", False, f"{text!r} -> {native!r} vs {sequential!r}")
				break
		else:
			check("random parity", True)

	# --- is_match
	with fast.WordFilter([("spam", "ham")]) as f:
		check("is_match hit", f.is_match("buy spam"), "")
		check("is_match miss", not f.is_match("buy ham"), "")
	with fast.WordFilter([]) as f:
		check("empty filter", f.replace("anything") == ("anything", False), "")
		check("empty filter match", not f.is_match("anything"), "")

	# --- sanitize parity with settings_store
	from ayugram_compat import settings_store

	for raw in ["com.example.plugin", "my plugin", "../evil", "._.", "", "a b.c", "__x__", "...", "плагин"]:
		native = fast.sanitize_id(raw)
		python = settings_store.sanitize(raw)
		check(f"sanitize {raw!r}", native == python, f"{native!r} vs {python!r}")

	# --- settings roundtrip and Python compatibility
	with tempfile.TemporaryDirectory() as directory:
		values = {"enabled": True, "name": "тест", "count": 3, "ratio": 0.5, "list": [1, 2]}
		check("settings write", fast.settings_write(directory, "demo", values), "")
		read, had_error = fast.settings_read(directory, "demo")
		check("settings read", read == values and not had_error, f"{read!r} {had_error}")

		path = Path(directory) / "demo.settings.json"
		document = json.loads(path.read_text(encoding="utf-8"))
		check("settings layout id", document.get("id") == "demo", repr(document.get("id")))
		check("settings layout version", document.get("version") == 1, repr(document.get("version")))
		check("settings layout body", document.get("settings") == values, repr(document.get("settings")))

		# The Python store must be able to read what Rust wrote.
		import os
		os.environ["AYU_PLUGINS_DIR"] = directory
		store = settings_store
		store._CACHE.clear()
		store._LOADED.clear()
		python_read = store.read("demo")
		check("python reads rust settings", python_read == values, f"{python_read!r}")

		# ...and the other way around. The Python store merges its change set on
		# top of the file, so the invariant is that Rust reads exactly what the
		# Python store left on disk.
		store.write("demo", {"enabled": False, "extra": "x"})
		on_disk = json.loads(path.read_text(encoding="utf-8"))["settings"]
		native_read, _ = fast.settings_read(directory, "demo")
		check("rust reads python settings", native_read == on_disk, f"{native_read!r} vs {on_disk!r}")

		errors_after, had_error = fast.settings_read(directory, "missing")
		check("missing settings", errors_after == {} and not had_error, f"{errors_after!r} {had_error}")

		(Path(directory) / "broken.settings.json").write_text("{nope", encoding="utf-8")
		_, had_error = fast.settings_read(directory, "broken")
		check("broken settings reports", had_error, "")

	# --- fingerprints
	with tempfile.TemporaryDirectory() as directory:
		path = Path(directory) / "file.bin"
		path.write_bytes(b"hello")
		first = fast.file_fingerprint(path)
		check("fingerprint", first is not None and first[1] == 5, repr(first))
		path.write_bytes(b"hellp")
		second = fast.file_fingerprint(path)
		check("fingerprint changes", first is not None and second is not None and first[0] != second[0], "")
		check("fingerprint missing", fast.file_fingerprint(Path(directory) / "nope") is None, "")
		import zlib
		big = b"".join(bytes([i % 256]) for i in range(200_000))
		check("fingerprint big", fast.fnv1a64(big) == fnv_python(big), "")

	# --- manifest problems parity
	documents = [
		'{"id":"abc","name":"N"}',
		'{"id":"1abc","name":"N"}',
		'{"id":"abc","name":"","entry":"main.py"}',
		'{"id":"abc","name":"N","api_version":9}',
		'{"id":"abc","name":"N","api_version":0}',
		'{"id":"abc","name":"N","entry":"../x.py"}',
		'{"id":"abc","name":"N","hooks":["on_made_up","on_message"]}',
		'{"id":"abc","name":"N","platforms":["android"]}',
		'{"id":"abc","name":"N","desktop_only":true}',
		"{not json",
		"[]",
	]
	for document in documents:
		native = fast.manifest_problems(document)
		python = fast._python_manifest_problems(document)
		# Message wording for invalid JSON differs by parser; the contract is
		# that both refuse it.
		if not _is_json(document):
			check(f"manifest {document[:40]}", bool(native) and bool(python),
			      f"\n    native={native}\n    python={python}")
			continue
		check(f"manifest {document[:40]}", native == python, f"\n    native={native}\n    python={python}")

	# --- benchmark: cheap sanity, not a gate
	# Fixed-width needles: no needle is a substring of another, so the only
	# work left is the scan itself.
	pairs = [(f"n{i:04d}", f"word{i}") for i in range(200)]
	text = " ".join(f"n{i % 200:04d}" for i in range(20_000))
	with fast.WordFilter(pairs) as f:
		start = time.perf_counter()
		f.replace(text)
		native_ms = (time.perf_counter() - start) * 1000
	start = time.perf_counter()
	naive_replace(text, pairs)
	naive_ms = (time.perf_counter() - start) * 1000
	print(f"bench: native {native_ms:.1f} ms, sequential python {naive_ms:.1f} ms, "
	      f"speedup {naive_ms / native_ms:.1f}x")

	print()
	if FAILURES:
		print(f"{len(FAILURES)} failure(s):")
		for line in FAILURES:
			print(" -", line)
		return 1
	print("all checks passed")
	return 0


def _is_json(document: str) -> bool:
	try:
		json.loads(document)
		return True
	except Exception:
		return False


def fnv_python(data: bytes) -> int:
	value = 0xCBF29CE484222325
	for byte in data:
		value ^= byte
		value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
	return value


if __name__ == "__main__":
	raise SystemExit(main())

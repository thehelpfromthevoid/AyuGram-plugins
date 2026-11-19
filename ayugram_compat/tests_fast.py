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

		# A settings map may itself contain a key called `settings`. The writer
		# must store the map it was handed, not mistake the nested object for
		# the file envelope: doing that dropped every sibling key on save.
		tricky = {"settings": {"a": 1}, "b": 2}
		check("settings write with a nested 'settings' key",
		      fast.settings_write(directory, "tricky", tricky), "")
		tricky_read, _ = fast.settings_read(directory, "tricky")
		check("nested 'settings' key round-trips", tricky_read == tricky,
		      f"{tricky_read!r} vs {tricky!r}")

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

	# --- the refusal text is the C++ host's, not just self-consistent.
	# Comparing native against python alone would pass even if both drifted
	# away from the client. These strings were read off the real header by
	# compiling it against `plugin_manifest.cpp`'s own sequence: nlohmann's
	# `value()` throws `type_error.306` for a document that is not an object,
	# and `get<int>()` throws `type_error.302` for a mistyped `api_version`.
	# The host catches either and refuses the manifest with that exact wording.
	canon_refusals = [
		("[]", 306, "array"),
		('"a string"', 306, "string"),
		("null", 306, "null"),
		("7", 306, "number"),
		("true", 306, "boolean"),
		('{"id":"abc","name":"N","api_version":"1"}', 302, "string"),
		('{"id":"abc","name":"N","api_version":null}', 302, "null"),
		('{"id":"abc","name":"N","api_version":[1]}', 302, "array"),
		('{"id":"abc","name":"N","api_version":{}}', 302, "object"),
	]
	for document, code, kind in canon_refusals:
		want = f"type_error.{code}"
		detail = (f"cannot use value() with {kind}" if code == 306
		          else f"type must be number, but is {kind}")
		native = fast.manifest_problems(document)
		python = fast._python_manifest_problems(document)
		ok_native = len(native) == 1 and want in native[0] and detail in native[0]
		ok_python = len(python) == 1 and want in python[0] and detail in python[0]
		check(f"canon refusal {document[:32]} (native)", ok_native,
		      f"\n    want [{want}] {detail}\n    native={native}")
		check(f"canon refusal {document[:32]} (python)", ok_python,
		      f"\n    want [{want}] {detail}\n    python={python}")

	# a float or boolean api_version is *cast*, not refused: the client loads it
	for document in [
		'{"id":"abc","name":"N","api_version":1.5}',
		'{"id":"abc","name":"N","api_version":1.9}',
		'{"id":"abc","name":"N","api_version":true}',
	]:
		check(f"canon cast {document[-24:]}", fast.manifest_problems(document) == [],
		      f"{fast.manifest_problems(document)!r}")

	# `desktop_only` is read with value(..., false), i.e. get<bool>(), which
	# throws for a non-boolean - and an empty `platforms` array still takes the
	# `is_array()` branch, so it suppresses `desktop_only` entirely. Both were
	# read off a compiled transcription of ParseManifestJson (canon_parse.txt).
	canon_boolean = [
		('{"id":"abc","name":"N","desktop_only":"yes"}', "string"),
		('{"id":"abc","name":"N","desktop_only":1}', "number"),
		('{"id":"abc","name":"N","desktop_only":null}', "null"),
		('{"id":"abc","name":"N","desktop_only":[]}', "array"),
		('{"id":"abc","name":"N","platforms":"desktop","desktop_only":"yes"}', "string"),
	]
	for document, kind in canon_boolean:
		want = f"type must be boolean, but is {kind}"
		native = fast.manifest_problems(document)
		python = fast._python_manifest_problems(document)
		check(f"canon boolean {document[-28:]} (native)",
		      len(native) == 1 and "type_error.302" in native[0] and want in native[0],
		      f"\n    want {want}\n    native={native}")
		check(f"canon boolean {document[-28:]} (python)",
		      len(python) == 1 and "type_error.302" in python[0] and want in python[0],
		      f"\n    want {want}\n    python={python}")

	# an empty platforms array is still "platforms present": desktop_only is not
	# consulted, so this manifest is fine rather than desktop-only
	empty_platforms = '{"id":"abc","name":"N","platforms":[],"desktop_only":true}'
	check("empty platforms suppresses desktop_only",
	      fast.manifest_problems(empty_platforms) == [],
	      repr(fast.manifest_problems(empty_platforms)))

	# A double `api_version` out of int32 range goes through the platform
	# conversion, which on the client's build clamps to INT_MIN instead of
	# saturating - the one case where Rust's `as` and Python disagreed. INT_MIN
	# is <= 0, so the resulting problem is the "positive number" one. If the
	# code saturated instead it would be INT_MAX and the answer would be
	# "api_version 2147483647 is newer than the app's 1", which is the visible
	# difference this asserts on. Values observed from the compiled header.
	for document in [
		'{"id":"abc","name":"N","api_version":1e10}',
		'{"id":"abc","name":"N","api_version":1e300}',
		'{"id":"abc","name":"N","api_version":-1e300}',
		'{"id":"abc","name":"N","api_version":2147483648.0}',
		'{"id":"abc","name":"N","api_version":18446744073709551616}',
		'{"id":"abc","name":"N","api_version":10000000000000000000000000000000}',
	]:
		want = ["api_version must be a positive number"]
		native = fast.manifest_problems(document)
		python = fast._python_manifest_problems(document)
		check(f"canon double api {document[-26:]} (native)", native == want,
		      f"\n    want {want}\n    native={native}")
		check(f"canon double api {document[-26:]} (python)", python == want,
		      f"\n    want {want}\n    python={python}")

	# ...whereas a double that stays in range is kept, truncating toward zero
	for document in [
		'{"id":"abc","name":"N","api_version":2147483647.5}',
		'{"id":"abc","name":"N","api_version":100.0}',
		'{"id":"abc","name":"N","api_version":-0.5}',
	]:
		native = fast.manifest_problems(document)
		python = fast._python_manifest_problems(document)
		check(f"canon double api in range {document[-24:]} native==python", native == python,
		      f"\n    native={native}\n    python={python}")

	# --- the warm call that keeps the native path alive in the real client.
	#
	# The bridge installs `sandbox/audithook.py` in ConfigureSandbox, before a
	# plugin's code, and that hook refuses `ctypes.dlopen` outright - no
	# permission lifts it. `ctypes.CDLL` raises exactly that event, and `_load`
	# catches `(OSError, AttributeError)`, which `PermissionError` is a subclass
	# of: the refusal was swallowed, cached for the life of the process, and
	# every fast call silently ran its Python fallback. Loading the library once
	# during the package's first import - inside the bridge's InitPython, before
	# the sandbox exists - keeps it mapped for the session.
	#
	# The behaviour itself cannot be observed from here, because this module
	# imports `ayugram_compat` (and therefore warms it) before any test runs.
	# What is asserted is that the hook exists and returns a usable answer, and
	# that the package still calls it at import time - the part that a refactor
	# would drop without any other test noticing.
	check("warm() reports the library state", isinstance(fast.warm(), bool),
	      repr(fast.warm()))
	check("warm() is idempotent", fast.warm() is fast.warm(), "")
	check("warm() agrees with available()", fast.warm() == fast.available(),
	      f"{fast.warm()} vs {fast.available()}")

	init_source = (Path(__file__).resolve().parents[1] / "ayugram_compat" / "__init__.py") \
		.read_text(encoding="utf-8")
	check("ayugram_compat/__init__ warms the native layer at import",
	      "_fast.warm()" in init_source,
	      "the warm call is what keeps ctypes.dlopen ahead of the sandbox audit hook")

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

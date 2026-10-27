__id__ = "neural_typing"
__name__ = "Neural Typing (Desktop)"
__description__ = "Р­С„С„РµРєС‚ РїРѕСЃС‚РµРїРµРЅРЅРѕРіРѕ РїРѕСЏРІР»РµРЅРёСЏ С‚РµРєСЃС‚Р° РїСЂРё РѕС‚РїСЂР°РІРєРµ: СЃРѕРѕР±С‰РµРЅРёРµ СѓС…РѕРґРёС‚ РїРµСЂРІС‹Рј РєСѓСЃРєРѕРј Рё РґРѕРїРёСЃС‹РІР°РµС‚СЃСЏ РїРѕ РѕРґРЅРѕРјСѓ СЃР»РѕРІСѓ С‡РµСЂРµР· РїСЂР°РІРєСѓ. Р›РѕРєР°Р»СЊРЅР°СЏ РєРѕСЃРјРµС‚РёРєР° вЂ” СЌС„С„РµРєС‚ СЃС‚СЂРёРјРёРЅРіР° РѕС‚РІРµС‚Р° РР."
__version__ = "1.4.0-desktop"
__author__ = "@van1lLove (РёРґРµСЏ: @calm_rio), Desktop port"
__min_version__ = "7.0.0"

import random
import threading
import time
import traceback
from typing import Optional

from base_plugin import BasePlugin
from client_utils import edit_message, send_text
from hook_utils import hook
from ui.settings import Divider, Header, Input, Switch

DEFAULT_TICK_MS = 40
DEFAULT_CHARS_PER_TICK = 1
DEFAULT_MAX_LEN = 4096
DEFAULT_START_PAUSE_MS = 500
DEFAULT_CONFIRM_TIMEOUT_S = 6.0
STEPS_TARGET = 45
CURSOR = "в–Ќ"
MIN_LEN = 3


def _attr(obj, name, default=None):
	if isinstance(obj, dict):
		return obj.get(name, default)
	return getattr(obj, name, default)


_CAPS: Optional[dict] = None


def _caps() -> dict:
	"""The effect needs both send_text and edit_message in the native bridge."""
	global _CAPS
	if _CAPS is None:
		try:
			import ayu_api

			_CAPS = {
				"send": hasattr(ayu_api, "send_text"),
				"edit": hasattr(ayu_api, "edit_message"),
			}
		except Exception:
			_CAPS = {"send": False, "edit": False}
		if not (_CAPS["send"] and _CAPS["edit"]):
			_log("[NeuralTyping] native bridge lacks send_text/edit_message, effect is off")
	return _CAPS


def _log(text: str) -> None:
	try:
		from ayu_api import log as _l

		_l(text)
	except Exception:
		print(text)


class NeuralTypingPlugin(BasePlugin):
	def on_plugin_load(self):
		self._pending = None
		self._timers = {}
		self._tk = 0
		self._lock = threading.RLock()
		self.log("[NeuralTyping] desktop port loaded (outgoing only)")

	def on_plugin_unload(self):
		with self._lock:
			timers = list(self._timers.values())
			self._timers.clear()
			self._pending = None
		for t in timers:
			try:
				t.cancel()
			except Exception:
				pass

	def _int_setting(self, key, default, lo, hi):
		try:
			return max(lo, min(hi, int(self.get_setting(key, str(default)))))
		except Exception:
			return default

	def _word_stops(self, full):
		stops = []
		i, n = 0, len(full)
		while i < n:
			j = i
			while j < n and not full[j].isspace():
				j += 1
			while j < n and full[j].isspace():
				j += 1
			stops.append(j)
			i = j
		return stops

	def _schedule(self, anim):
		tick = self._int_setting("tick_ms", DEFAULT_TICK_MS, 10, 500) / 1000.0
		if anim["pos"] > 0 and not anim.get("paused") and self.get_setting("start_pause", False):
			anim["paused"] = True
			tick = self._int_setting("start_pause_ms", DEFAULT_START_PAUSE_MS, 100, 3000) / 1000.0
		elif self.get_setting("jitter", True):
			tick *= random.uniform(0.6, 1.5)
		t = threading.Timer(tick, self._step, args=(anim["key"],))
		t.daemon = True
		with self._lock:
			if anim.get("stopped") or self._pending is not anim:
				return
			self._timers[anim["key"]] = t
		t.start()

	def _step(self, key):
		with self._lock:
			self._timers.pop(key, None)
			anim = self._pending
		if anim is None or anim["key"] != key or not anim["msg_id"]:
			return
		try:
			if anim["stops"]:
				anim["stop_idx"] += anim["words_per_step"]
				idx = anim["stop_idx"]
				anim["pos"] = anim["stops"][idx] if idx < len(anim["stops"]) else len(anim["full"])
			else:
				anim["pos"] += anim["step_chars"]
			if anim["pos"] >= len(anim["full"]):
				edit_message(anim["peer"], anim["msg_id"], anim["full"], True)
				self.log(f"[NeuralTyping] done in {anim['peer']}")
				self._stop(anim)
				return
			payload = anim["full"][:anim["pos"]]
			if self.get_setting("cursor", True):
				payload += CURSOR
			if not edit_message(anim["peer"], anim["msg_id"], payload):
				self._recover(anim)
				return
			self._schedule(anim)
		except Exception as e:
			self.log(f"[NeuralTyping] step failed: {e}\n{traceback.format_exc()}")
			self._recover(anim)

	def _recover(self, anim):
		"""Stop cleanly. Never resend: the half-written bubble is still in the
		chat, and sending the text again added a second bubble for every step,
		which is how one message turned into a wall of them."""
		self.log(f"[NeuralTyping] animation stopped for peer {anim['peer']}")
		self._stop(anim)

	def _stop(self, anim):
		with self._lock:
			anim["stopped"] = True
			if self._pending is anim:
				self._pending = None
			timer = self._timers.pop(anim["key"], None)
			self._timers.pop(("confirm", anim["key"]), None)
		if timer is not None:
			try:
				timer.cancel()
			except Exception:
				pass

	def _arm(self, anim):
		with self._lock:
			previous = self._pending
			self._pending = anim
			self._timers[anim["key"]] = None
		if previous is not None:
			self._stop(previous)

	def _watch_confirm(self, anim):
		timeout = float(self.get_setting("confirm_timeout_s", DEFAULT_CONFIRM_TIMEOUT_S) or DEFAULT_CONFIRM_TIMEOUT_S)

		def _loop():
			deadline = time.time() + timeout
			while time.time() < deadline:
				time.sleep(0.1)
				with self._lock:
					if self._pending is not anim:
						return
					if anim["msg_id"]:
						break
			with self._lock:
				current = self._pending
			if current is not anim:
				return
			if not anim["msg_id"]:
				# The server never echoed the bubble back, so there is no id to
				# edit. It already holds the first slice, so send only what is
				# left: nothing is lost and nothing is duplicated.
				rest = anim["full"][anim["sent_len"]:]
				self.log(
					f"[NeuralTyping] no confirmation for peer {anim['peer']}, "
					f"sending the remaining {len(rest)} chars")
				self._stop(anim)
				if rest.strip():
					try:
						send_text(anim["peer"], rest)
					except Exception as e:
						self.log(f"[NeuralTyping] tail send failed: {e}")
				return
			self._schedule(anim)

		t = threading.Thread(target=_loop, daemon=True)
		with self._lock:
			self._timers[("confirm", anim["key"])] = t
		t.start()

	def on_plugin_message(self, msg):
		with self._lock:
			anim = self._pending
		if anim is None or anim["msg_id"]:
			return
		if not _attr(msg, "out"):
			return
		if int(_attr(msg, "peer_id", 0) or 0) != anim["peer"]:
			return
		anim["msg_id"] = int(_attr(msg, "msg_id", 0) or 0)

	@hook("on_message")
	def _on_message(self, msg):
		self.on_plugin_message(msg)

	@hook("on_message_sending")
	def _on_sending(self, ctx):
		try:
			if not self.get_setting("enabled", True):
				return
			peer = int(_attr(_attr(ctx, "message"), "peer_id", 0) or _attr(ctx, "peer_id", 0) or 0)
			text = _attr(_attr(ctx, "message"), "text", "") or ""
			if not isinstance(text, str):
				return
			if anim := self._skip(ctx, text, peer):
				return
			self._cancel(ctx)
			self._start(peer, text)
		except Exception as e:
			self.log(f"[NeuralTyping] sending hook failed: {e}\n{traceback.format_exc()}")

	def _skip(self, ctx, text, peer):
		if not _caps()["edit"]:
			return True
		max_len = self._int_setting("max_len", DEFAULT_MAX_LEN, 10, 4096)
		stripped = text.strip()
		if len(stripped) < MIN_LEN or len(text) > max_len:
			return True
		if self.get_setting("skip_commands", True) and stripped.startswith("/"):
			return True
		if self.get_setting("only_private", False) and peer >= 0:
			return True
		if self.get_setting("skip_code", True) and "```" in text:
			return True
		return False

	def _cancel(self, ctx):
		if isinstance(ctx, dict):
			ctx["cancel"] = True
		elif hasattr(ctx, "cancel"):
			ctx.cancel = True

	def _start(self, peer, text):
		full = text
		base = self._int_setting("chars_per_tick", DEFAULT_CHARS_PER_TICK, 1, 50)
		step_chars = max(base, -(-len(full) // STEPS_TARGET))
		stops = self._word_stops(full) if self.get_setting("by_words", False) else None
		if stops:
			words_per_step = max(1, -(-len(stops) // STEPS_TARGET))
			stop_idx = words_per_step - 1
			init_pos = stops[min(stop_idx, len(stops) - 1)]
		else:
			words_per_step = 1
			stop_idx = 0
			init_pos = step_chars
		self._tk += 1
		anim = {
			"key": self._tk,
			"peer": peer,
			"full": full,
			"pos": init_pos,
			"step_chars": step_chars,
			"stops": stops,
			"stop_idx": stop_idx,
			"words_per_step": words_per_step,
			"msg_id": 0,
			"sent_len": init_pos,
			"paused": False,
		}
		first = full[:init_pos]
		if self.get_setting("cursor", True):
			first += CURSOR
		send_text(peer, first)
		self._arm(anim)
		self._watch_confirm(anim)

	def create_settings(self):
		return [
			Header(text="Neural Typing (Desktop)"),
			Switch(
				key="enabled",
				text="Р’РєР»СЋС‡РµРЅРѕ",
				subtext="РџРµСЂРµС…РІР°С‚С‹РІР°РµС‚ РІР°С€Сѓ РѕС‚РїСЂР°РІРєСѓ Рё РїРµС‡Р°С‚Р°РµС‚ С‚РµРєСЃС‚ РїРѕ С‡Р°СЃС‚СЏРј",
				default=True,
			),
			Switch(
				key="cursor",
				text="РљСѓСЂСЃРѕСЂ РїСЂРё РїРµС‡Р°С‚Рё",
				subtext=f"РџРѕРєР°Р·С‹РІР°С‚СЊ В«{CURSOR}В» РІ РєРѕРЅС†Рµ РїРѕСЏРІР»СЏСЋС‰РµРіРѕСЃСЏ С‚РµРєСЃС‚Р°",
				default=True,
			),
			Switch(
				key="by_words",
				text="РџРѕ СЃР»РѕРІР°Рј",
				subtext="РўРµРєСЃС‚ РїРѕСЏРІР»СЏРµС‚СЃСЏ С†РµР»С‹РјРё СЃР»РѕРІР°РјРё, Р° РЅРµ РїРѕ Р±СѓРєРІРµ",
				default=False,
			),
			Switch(
				key="jitter",
				text="Р–РёРІР°СЏ СЃРєРѕСЂРѕСЃС‚СЊ",
				subtext="РќРµСЂР°РІРЅРѕРјРµСЂРЅС‹Р№ РёРЅС‚РµСЂРІР°Р» вЂ” РІС‹РіР»СЏРґРёС‚ РєР°Рє РЅР°СЃС‚РѕСЏС‰РёР№ СЃС‚СЂРёРјРёРЅРі",
				default=True,
			),
			Switch(
				key="start_pause",
				text="РџР°СѓР·Р° РїРѕСЃР»Рµ РїРµСЂРІС‹С… СЃРёРјРІРѕР»РѕРІ",
				subtext="РџРµСЂРІС‹Рµ СЃРёРјРІРѕР»С‹, РєРѕСЂРѕС‚РєР°СЏ РїР°СѓР·Р°, РґР°Р»СЊС€Рµ СЂРѕРІРЅР°СЏ РїРµС‡Р°С‚СЊ",
				default=False,
			),
			Switch(
				key="only_private",
				text="РўРѕР»СЊРєРѕ Р»РёС‡РЅС‹Рµ С‡Р°С‚С‹",
				subtext="РќРµ Р°РЅРёРјРёСЂРѕРІР°С‚СЊ СЃРѕРѕР±С‰РµРЅРёСЏ РІ РіСЂСѓРїРїР°С… Рё РєР°РЅР°Р»Р°С…",
				default=False,
			),
			Switch(
				key="skip_commands",
				text="РџСЂРѕРїСѓСЃРєР°С‚СЊ РєРѕРјР°РЅРґС‹",
				subtext="РЎРѕРѕР±С‰РµРЅРёСЏ, РЅР°С‡РёРЅР°СЋС‰РёРµСЃСЏ СЃ В«/В», СѓС…РѕРґСЏС‚ Р±РµР· СЌС„С„РµРєС‚Р°",
				default=True,
			),
			Switch(
				key="skip_code",
				text="РџСЂРѕРїСѓСЃРєР°С‚СЊ Р±Р»РѕРєРё РєРѕРґР°",
				subtext="РЎРѕРѕР±С‰РµРЅРёСЏ СЃ ``` РЅРµ Р°РЅРёРјРёСЂСѓСЋС‚СЃСЏ",
				default=True,
			),
			Divider(),
			Input(
				key="chars_per_tick",
				text="РЎРёРјРІРѕР»РѕРІ Р·Р° С€Р°Рі",
				default=str(DEFAULT_CHARS_PER_TICK),
				subtext="РЎРєРѕР»СЊРєРѕ СЃРёРјРІРѕР»РѕРІ РїРѕСЏРІР»СЏРµС‚СЃСЏ Р·Р° РѕРґРёРЅ С€Р°Рі (1вЂ“50)",
			),
			Input(
				key="tick_ms",
				text="РРЅС‚РµСЂРІР°Р» С€Р°РіР° (РјСЃ)",
				default=str(DEFAULT_TICK_MS),
				subtext="РџР°СѓР·Р° РјРµР¶РґСѓ С€Р°РіР°РјРё, 10вЂ“500 РјСЃ",
			),
			Input(
				key="start_pause_ms",
				text="Р”Р»РёС‚РµР»СЊРЅРѕСЃС‚СЊ РїР°СѓР·С‹ (РјСЃ)",
				default=str(DEFAULT_START_PAUSE_MS),
				subtext="РџР°СѓР·Р° РїРѕСЃР»Рµ РїРµСЂРІС‹С… СЃРёРјРІРѕР»РѕРІ, 100вЂ“3000 РјСЃ",
			),
			Input(
				key="confirm_timeout_s",
				text="РћР¶РёРґР°РЅРёРµ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ (СЃ)",
				default=str(DEFAULT_CONFIRM_TIMEOUT_S),
				subtext="РЎРєРѕР»СЊРєРѕ Р¶РґР°С‚СЊ id РѕС‚РїСЂР°РІР»РµРЅРЅРѕРіРѕ СЃРѕРѕР±С‰РµРЅРёСЏ, РїСЂРµР¶РґРµ С‡РµРј РѕС‚РјРµРЅРёС‚СЊ СЌС„С„РµРєС‚",
			),
			Input(
				key="max_len",
				text="РњР°РєСЃРёРјР°Р»СЊРЅР°СЏ РґР»РёРЅР°",
				default=str(DEFAULT_MAX_LEN),
				subtext="РЎРѕРѕР±С‰РµРЅРёСЏ РґР»РёРЅРЅРµРµ РЅРµ Р°РЅРёРјРёСЂСѓСЋС‚СЃСЏ",
			),
		]

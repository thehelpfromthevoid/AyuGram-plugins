"""Macros — create/save/run text macros via chat commands.

Commands (outgoing):
  /macro save <name> <text>  — save macro
  /macro del <name>          — delete macro
  /macro list                — list macros (toast)
  /m <name>                  — expand and send macro text
  /macro <name>              — same as /m
"""
try:
	from ayugram_compat.hook_utils import hook
	from ayugram_compat.android_utils import toast
except ImportError:
	from ayu_api import hook, toast

import json
import os
import re

_STORE = None


def _path():
	base = os.environ.get("AYU_PLUGINS_DATA") or os.path.join(
		os.path.expanduser("~"), ".ayugram", "plugins_data")
	os.makedirs(base, exist_ok=True)
	return os.path.join(base, "macros.json")


def _load():
	global _STORE
	if _STORE is not None:
		return _STORE
	p = _path()
	try:
		with open(p, "r", encoding="utf-8") as f:
			_STORE = json.load(f)
			if not isinstance(_STORE, dict):
				_STORE = {}
	except Exception:
		_STORE = {}
	return _STORE


def _save():
	p = _path()
	try:
		with open(p, "w", encoding="utf-8") as f:
			json.dump(_load(), f, ensure_ascii=False, indent=2)
	except Exception as e:
		toast(f"Macros save error: {e}")


def _set_rewrite(ctx, text: str):
	if isinstance(ctx, dict):
		ctx["rewrite"] = True
		ctx["rewritten_text"] = text
		ctx["rewrittenText"] = text
	else:
		ctx.rewrite = True
		if hasattr(ctx, "rewrittenText"):
			ctx.rewrittenText = text
		if hasattr(ctx, "rewritten_text"):
			ctx.rewritten_text = text


def _set_cancel(ctx):
	if isinstance(ctx, dict):
		ctx["cancel"] = True
	elif hasattr(ctx, "cancel"):
		ctx.cancel = True


@hook("on_message_sending")
def on_sending(ctx):
	msg = ctx["message"] if isinstance(ctx, dict) else ctx.message
	text = msg.get("text", "") if isinstance(msg, dict) else getattr(msg, "text", "")
	if not isinstance(text, str):
		return
	raw = text.strip()
	if not raw:
		return

	store = _load()

	m_save = re.match(r"(?is)^/macro\s+save\s+(\S+)\s+(.+)$", raw)
	if m_save:
		name, body = m_save.group(1).lower(), m_save.group(2).strip()
		store[name] = body
		_save()
		_set_cancel(ctx)
		toast(f"Макрос «{name}» сохранён")
		return

	m_del = re.match(r"(?is)^/macro\s+del(?:ete)?\s+(\S+)\s*$", raw)
	if m_del:
		name = m_del.group(1).lower()
		if name in store:
			del store[name]
			_save()
			toast(f"Макрос «{name}» удалён")
		else:
			toast(f"Макрос «{name}» не найден")
		_set_cancel(ctx)
		return

	if re.match(r"(?is)^/macro\s+list\s*$", raw) or raw.lower() == "/macros":
		if not store:
			toast("Макросов нет. /macro save имя текст")
		else:
			names = ", ".join(sorted(store.keys())[:40])
			toast(f"Макросы: {names}")
		_set_cancel(ctx)
		return

	m_run = re.match(r"(?is)^/(?:m|macro)\s+(\S+)\s*$", raw)
	if m_run:
		name = m_run.group(1).lower()
		if name in ("save", "del", "delete", "list"):
			return
		body = store.get(name)
		if body is None:
			toast(f"Макрос «{name}» не найден")
			_set_cancel(ctx)
			return
		_set_rewrite(ctx, body)
		return

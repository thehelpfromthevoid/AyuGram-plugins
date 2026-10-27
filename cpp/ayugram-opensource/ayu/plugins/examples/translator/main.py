"""Р’СЃС‚СЂРѕРµРЅРЅС‹Р№ РїРµСЂРµРІРѕРґ РёСЃС…РѕРґСЏС‰РёС…: /en /ru /tr вЂ” РґРµРјРѕ-РїР»Р°РіРёРЅ.

РќР°С‚РёРІРЅРѕРµ РїСЂРёР»РѕР¶РµРЅРёРµ СѓР¶Рµ СѓРјРµРµС‚ С‚Рµ Р¶Рµ РєРѕРјР°РЅРґС‹ (РќР°СЃС‚СЂРѕР№РєРё в†’ AyuGram в†’ РћР±С‰РёРµ).
Р­С‚РѕС‚ РїР»Р°РіРёРЅ РѕСЃС‚Р°С‘С‚СЃСЏ РєР°Рє РїСЂРёРјРµСЂ on_message_sending; РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ РІС‹РєР»СЋС‡РµРЅ.
"""
try:
	from ayugram_compat.hook_utils import hook
	from ayugram_compat.android_utils import toast
except ImportError:
	from ayu_api import hook, toast

import re
import urllib.parse
import urllib.request


def _translate(text: str, to_lang: str) -> str:
	q = urllib.parse.quote(text)
	url = (
		"https://translate.googleapis.com/translate_a/single"
		f"?client=gtx&sl=auto&tl={urllib.parse.quote(to_lang)}&dt=t&q={q}"
	)
	req = urllib.request.Request(url, headers={"User-Agent": "AyuGramDesktop/1.0"})
	with urllib.request.urlopen(req, timeout=2) as resp:
		raw = resp.read().decode("utf-8", "replace")
	# rough parse: [[["translated","orig",...],...],...]
	import json
	data = json.loads(raw)
	parts = []
	for chunk in data[0]:
		if chunk and chunk[0]:
			parts.append(chunk[0])
	out = "".join(parts).strip()
	return out or text


@hook("on_message_sending")
def on_sending(ctx):
	try:
		msg = ctx["message"] if isinstance(ctx, dict) else getattr(ctx, "message", None)
	except Exception:
		return
	if isinstance(msg, dict):
		text = msg.get("text", msg.get("message", ""))
	else:
		text = getattr(msg, "text", getattr(msg, "message", ""))
	if not isinstance(text, str):
		return
	raw = text.strip()
	to_lang = None
	payload = None
	m = re.match(r"(?is)^/(en|ru)\s+(.+)$", raw)
	if m:
		to_lang, payload = m.group(1).lower(), m.group(2).strip()
	else:
		m2 = re.match(r"(?is)^/tr\s+(\S+)\s+(.+)$", raw)
		if m2:
			to_lang, payload = m2.group(1).lower(), m2.group(2).strip()
	if not to_lang or not payload:
		return
	try:
		new = _translate(payload, to_lang)
	except Exception as e:
		toast(f"РџРµСЂРµРІРѕРґ РЅРµ СѓРґР°Р»СЃСЏ: {e}")
		return
	if isinstance(ctx, dict):
		ctx["rewrite"] = True
		ctx["rewritten_text"] = new
		ctx["rewrittenText"] = new
	else:
		ctx.rewrite = True
		if hasattr(ctx, "rewrittenText"):
			ctx.rewrittenText = new
		if hasattr(ctx, "rewritten_text"):
			ctx.rewritten_text = new
	toast("РџРµСЂРµРІРµРґРµРЅРѕ (РїР»Р°РіРёРЅ)")

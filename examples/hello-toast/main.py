try:
    from ayugram_compat.hook_utils import hook
    from ayugram_compat.android_utils import toast
except ImportError:
    from ayu_api import hook, toast


@hook("on_message")
def on_message(msg):
    try:
        if isinstance(msg, dict):
            text = msg.get("text", msg.get("message", "")) or ""
            peer = msg.get("peer_id", msg.get("peerId", 0))
        else:
            text = getattr(msg, "text", getattr(msg, "message", "")) or ""
            peer = getattr(msg, "peer_id", getattr(msg, "peerId", 0))
        if not isinstance(text, str):
            text = str(text)
        low = text.lower()
        if "hello" in low or "привет" in low:
            toast(f"Hello from {peer}: {text[:64]}")
    except Exception:
        pass

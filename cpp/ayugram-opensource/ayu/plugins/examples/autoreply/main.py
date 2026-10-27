"""AutoReply template: answers incoming messages matching triggers.

Portable: uses on_message (read) + client_utils.send_text (write).
Edit TRIGGERS for your own replies. Disabled by default.
"""
try:
    from ayugram_compat.hook_utils import hook
    from ayugram_compat.client_utils import send_text
    from ayugram_compat.android_utils import log
except ImportError:
    from ayu_api import hook, log
    from client_utils import send_text

TRIGGERS = {
    "привет": "Привет! Я на месте 👋",
    "ping": "pong",
}


@hook("on_message")
def reply(msg):
    try:
        if isinstance(msg, dict):
            text = msg.get("text", "") or ""
            peer = msg.get("peer_id", 0) or 0
            out = msg.get("out", False)
        else:
            text = getattr(msg, "text", "") or ""
            peer = getattr(msg, "peer_id", 0) or 0
            out = getattr(msg, "out", False)
        if out or not isinstance(text, str):
            return
        answer = TRIGGERS.get(text.strip().lower())
        if answer and peer:
            send_text(peer, answer)
    except Exception as e:
        log(f"autoreply error: {e}")

"""WordFilter template: replaces words in YOUR outgoing messages.

Example: type "brb" -> friends see "be right back".
Edit REPLACEMENTS. Disabled by default.
"""
try:
    from ayugram_compat.hook_utils import hook
except ImportError:
    from ayu_api import hook

REPLACEMENTS = {
    "brb": "be right back",
    "imo": "in my opinion",
}


@hook("on_message_sending")
def filtr(ctx):
    if isinstance(ctx, dict):
        msg = ctx.get("message")
        text = msg.get("text", "") if isinstance(msg, dict) else ""
    else:
        text = getattr(getattr(ctx, "message", None), "text", "") or ""
    if not isinstance(text, str) or not text:
        return
    new = text
    for short, full in REPLACEMENTS.items():
        new = new.replace(short, full)
    if new != text:
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

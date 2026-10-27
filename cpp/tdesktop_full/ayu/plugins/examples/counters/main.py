"""Counters template: /stats shows your session counters.

Counts incoming/outgoing messages. State resets on reload.
Disabled by default.
"""
try:
    from ayugram_compat.hook_utils import hook, command
    from ayugram_compat.android_utils import toast
except ImportError:
    from ayu_api import hook, command, toast

_stats = {"in": 0, "out": 0}


@hook("on_message")
def count_in(msg):
    try:
        out = msg.get("out", False) if isinstance(msg, dict) else getattr(msg, "out", False)
        if not out:
            _stats["in"] += 1
    except Exception:
        pass


@hook("on_message_sending")
def count_out(ctx):
    _stats["out"] += 1


@command("/stats")
def stats(peer_id, text, args):
    msg = f"Входящие: {_stats['in']}\nИсходящие: {_stats['out']}"
    toast(msg)
    return False

"""Ping-pong template: @command reception.

Type `/ping hello` in any chat — the message is replaced before send.
Copy this folder, change the id in plugin.json, write your own commands.
"""
try:
    from ayugram_compat.hook_utils import command
    from ayugram_compat.android_utils import toast
except ImportError:
    from ayu_api import command, toast


@command("/ping", aliases=("/p",))
def ping(peer_id, text, args):
    what = " ".join(args) if args else "pong"
    toast(f"pong: {what}")
    return f"pong: {what}"


@command("/shrug")
def shrug(peer_id, text, args):
    return "¯\\_(ツ)_/¯"

"""manifest.json + bot.py template: mobile-style layout.

Any manifest name (plugin.json/manifest.json) and any entry script
(main.py/__init__.py/plugin.py/index.py/bot.py) are accepted.
"""
try:
    from ayugram_compat.hook_utils import hook
    from ayugram_compat.android_utils import toast
except ImportError:
    from ayu_api import hook, toast


def on_load():
    toast("manifest_bot loaded")


@hook("on_message")
def echo(msg):
    text = getattr(msg, "text", "") or ""
    if text.lower().startswith("echo "):
        toast(f"echo: {text[5:64]}")

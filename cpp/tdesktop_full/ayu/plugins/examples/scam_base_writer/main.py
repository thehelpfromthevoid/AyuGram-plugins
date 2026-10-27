import re

from base_plugin import BasePlugin, HookResult, activate_base_plugins


__id__ = "scam_base_writer"
__name__ = "Scam Base Writer"
__description__ = "Warns on outgoing messages that look like common scam patterns"
__author__ = "@ayugram"
__version__ = "1.0.0"

_PATTERNS = [
    re.compile(r"send\s+(me\s+)?(seed|mnemonic|private\s*key)", re.I),
    re.compile(r"connect\s+wallet.*(airdrop|claim)", re.I),
    re.compile(r"(bit\.ly|tinyurl\.com)/\S+", re.I),
    re.compile(r"выведи\s+(деньги|средства).*(срочно|код)", re.I),
]


class ScamBaseWriterPlugin(BasePlugin):
    def on_plugin_load(self):
        self.add_on_send_message_hook()
        self.log("scam_base_writer loaded")

    def on_send_message_hook(self, account, params):
        text = None
        if isinstance(params, dict):
            text = params.get("message") or params.get("text")
        else:
            text = getattr(params, "message", None)
            if text is None:
                text = getattr(params, "text", None)
        if not isinstance(text, str) or not text.strip():
            return HookResult()
        for pat in _PATTERNS:
            if pat.search(text):
                self.toast("Scam pattern detected — check the message before sending")
                self.log(f"scam pattern matched: {pat.pattern}")
                break
        return HookResult()


try:
    activate_base_plugins(globals())
except Exception:
    pass

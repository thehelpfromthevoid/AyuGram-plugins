from datetime import datetime, timezone

from base_plugin import BasePlugin, HookResult, HookStrategy, activate_base_plugins


__id__ = "world_time"
__name__ = "World Time"
__description__ = "Reply to .time / .время with local and UTC time"
__author__ = "@ayugram"
__version__ = "1.0.0"


class WorldTimePlugin(BasePlugin):
    def on_plugin_load(self):
        self.add_on_send_message_hook()
        self.log("world_time loaded")

    def on_send_message_hook(self, account, params):
        text = None
        if isinstance(params, dict):
            text = params.get("message") or params.get("text")
        else:
            text = getattr(params, "message", None)
            if text is None:
                text = getattr(params, "text", None)
        if not isinstance(text, str):
            return HookResult()
        raw = text.strip()
        low = raw.lower()
        if not (low.startswith(".time") or low.startswith(".время") or low.startswith("/time")):
            return HookResult()
        local = datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z").strip()
        utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        reply = f"Local: {local or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nUTC: {utc}"
        self.toast(reply)
        if isinstance(params, dict):
            params["message"] = reply
        elif hasattr(params, "message"):
            params.message = reply
        return HookResult(strategy=HookStrategy.MODIFY, params=params)


try:
    activate_base_plugins(globals())
except Exception:
    pass

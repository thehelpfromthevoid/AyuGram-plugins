try:
    from ayugram_compat.hook_utils import hook
    from ayugram_compat.android_utils import toast, log
except ImportError:
    from ayu_api import hook, log, toast

_cache = {}
_CACHE_LIMIT = 2000


def _msg_fields(msg):
    if isinstance(msg, dict):
        return (
            msg.get("msg_id", msg.get("msgId", 0)),
            msg.get("peer_id", msg.get("peerId", 0)),
            msg.get("text", msg.get("message", "")),
        )
    return (
        getattr(msg, "msg_id", getattr(msg, "msgId", 0)),
        getattr(msg, "peer_id", getattr(msg, "peerId", 0)),
        getattr(msg, "text", getattr(msg, "message", "")),
    )


@hook("on_message")
def cache(msg):
    try:
        mid, peer, text = _msg_fields(msg)
        if not isinstance(mid, int) or mid <= 0:
            return
        if len(_cache) >= _CACHE_LIMIT:
            _cache.pop(next(iter(_cache)))
        _cache[mid] = (peer, text if isinstance(text, str) else str(text or ""))
    except Exception:
        pass


def _deleted_ids(ctx):
    try:
        if isinstance(ctx, dict):
            ids = ctx.get("msg_ids", ctx.get("msgIds", ctx.get("msgId", [])))
            if isinstance(ids, int):
                return [ids]
            return list(ids or [])
        for attr in ("msg_ids", "msgIds", "msgId"):
            v = getattr(ctx, attr, None)
            if v is not None:
                return [v] if isinstance(v, int) else list(v)
    except Exception:
        pass
    return []


@hook("on_message_deleted")
def restore(ctx):
    for i in _deleted_ids(ctx):
        try:
            if i in _cache:
                peer, text = _cache[i]
                log(f"deleted in {peer}: {str(text)[:200]}")
                toast("Message deleted, see logs")
        except Exception:
            pass

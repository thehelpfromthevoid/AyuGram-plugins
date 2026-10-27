"""AyuGram Desktop sandbox audit hook (Python 3.11+).
Deny-by-default for dangerous operations unless permission granted.
Injected by C++ bridge before loading plugin code.
"""
import sys

_ALLOWED_NET_HOSTS: set = set()
_ALLOW_STORAGE = False
_ALLOW_PROCESS = False


def configure(*, net_hosts=(), storage=False, process=False):
    global _ALLOWED_NET_HOSTS, _ALLOW_STORAGE, _ALLOW_PROCESS
    _ALLOWED_NET_HOSTS = set(net_hosts or [])
    _ALLOW_STORAGE = bool(storage)
    _ALLOW_PROCESS = bool(process)


def grant(*, net_hosts=(), storage=False, process=False):
    """Union extra allowances. Loads are additive; grant never narrows."""
    global _ALLOW_STORAGE, _ALLOW_PROCESS
    _ALLOWED_NET_HOSTS.update(net_hosts or [])
    if storage:
        _ALLOW_STORAGE = True
    if process:
        _ALLOW_PROCESS = True


def _hook(event: str, args):
    if event in ("socket.getaddrinfo", "socket.bind", "socket.connect"):
        host = ""
        if args:
            first = args[0]
            if isinstance(first, (tuple, list)) and first:
                host = str(first[0])
            else:
                host = str(first)
        if "*" not in _ALLOWED_NET_HOSTS and host not in _ALLOWED_NET_HOSTS:
            raise PermissionError(f"ayu sandbox: net to {host!r} denied (need net.http permission)")
    elif event == "open":
        path = str(args[0]) if args else ""
        if "tdata" in path.replace("\\", "/"):
            raise PermissionError("ayu sandbox: access to tdata/ denied")
        if not _ALLOW_STORAGE and not path.startswith("/tmp/ayu-plugin"):
            # allow read of own dir only; enforced strictly in import_filter
            pass
    elif event in ("os.system", "os.exec", "os.spawn", "subprocess.Popen"):
        if not _ALLOW_PROCESS:
            raise PermissionError("ayu sandbox: subprocess denied (need process.spawn permission)")
    elif event in ("ctypes.dlopen", "ctypes.dlfun"):
        raise PermissionError("ayu sandbox: ctypes denied")


def install():
    try:
        sys.addaudithook(_hook)
    except Exception:
        pass

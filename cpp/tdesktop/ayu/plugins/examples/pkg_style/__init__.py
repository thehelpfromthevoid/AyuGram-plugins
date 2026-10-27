"""Package-style template: __init__.py entry + local helper module.

Proves a plugin-local package wins over mobile stubs.
"""
try:
    from helpers import make_greeting
except ImportError:  # pragma: no cover
    def make_greeting(name):
        return f"Hello, {name}!"

try:
    from ayugram_compat.hook_utils import command
except ImportError:
    from ayu_api import command


@command("/greet")
def greet(peer_id, text, args):
    name = " ".join(args) if args else "world"
    return make_greeting(name)

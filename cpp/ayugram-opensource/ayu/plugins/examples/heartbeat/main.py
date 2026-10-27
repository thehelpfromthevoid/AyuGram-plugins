"""Heartbeat template: @interval + @on_start reception.

Logs every minute so you can verify the plugin is alive.
No chat spam by design.
"""
try:
    from ayugram_compat.hook_utils import interval, on_start
    from ayugram_compat.android_utils import toast, log
except ImportError:
    from ayu_api import interval, on_start, toast, log

import time

_started = time.time()


@on_start
def hello():
    toast("heartbeat: plugin started")


@interval(60)
def beat():
    up = int(time.time() - _started)
    log(f"heartbeat: alive {up // 60}m {up % 60}s")

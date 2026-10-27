# Security model

1. Manifest declares `permissions`. UI shows them before enable.
2. `sys.addaudithook` (`sandbox/audithook.py`) blocks `socket/open/subprocess/ctypes`,
   denies `tdata/` always.
3. `MetaPathFinder` (`sandbox/import_filter.py`) allowlists stdlib, requires
   permission for `os/socket/requests/subprocess`.
4. Levels: L0 in-process (signed/builtin only), L1 subinterpreter per plugin,
   L2 out-of-process worker (roadmap, QLocalSocket JSON-RPC).
5. Timeouts: hook > 500ms logged + skipped, exception isolated, plugin -> Error state.
6. No API exposes `authKey`, `tdata` path, or raw MTProto sender. Only high-level
   `Message/Chat/toast/log`.
7. GPL: bridge contract is open (`docs/*` + `ayu_api`), plugins are separate works.
   Do not bundle proprietary wheels in repo.

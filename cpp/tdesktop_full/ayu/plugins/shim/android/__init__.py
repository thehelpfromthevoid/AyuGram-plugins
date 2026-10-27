"""Stub package so `import android.*` fails with a clear Desktop message."""

raise ImportError(
    "Android-only plugin: Android APIs are not available on Desktop AyuGram."
)

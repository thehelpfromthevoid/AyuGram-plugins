"""Android-only plugin stub for Desktop packaging.

The real animtou plugin hooks Android View.dispatchTouchEvent via Java.
Desktop ships this stub so the plugin is listed with a clear error instead of
a raw ModuleNotFoundError: No module named 'java'.
"""

raise ImportError(
    "Android-only plugin (animtou): touch-ripple hooks need Java/Android. "
    "Install AyuGram for Android to use this plugin."
)

from .base import Plugin, PluginError, Registry, registry, render_markup

from . import builtin, extras  # noqa: E402,F401  (registers the built-in plugins)

__all__ = ["Plugin", "PluginError", "Registry", "registry", "render_markup"]

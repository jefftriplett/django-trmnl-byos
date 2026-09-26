import json
import re
import urllib.request

from django.template import Context, Engine
from django.template.loader import render_to_string

from .. import conf

DEFAULT_ICON = "https://trmnl.com/images/plugins/trmnl--render.svg"


class PluginError(Exception):
    pass


class Plugin:
    """Base class for plugins.

    A plugin turns a PluginInstance into markup for one view size (full,
    half_horizontal, half_vertical, quadrant). It returns only the ``layout``
    (and optionally its own ``title_bar``); the composer adds the screen,
    mashup and view wrappers, the same way trmnl.com does.
    """

    key = ""
    name = ""
    description = ""
    icon = DEFAULT_ICON
    template_name = None
    polls = False
    default_settings = {}

    def settings_for(self, instance):
        return {**self.default_settings, **(instance.settings or {})}

    def fetch(self, instance):
        """Return fresh merge variables for a polling plugin."""
        raise NotImplementedError

    def get_context(self, instance, size, trmnl):
        merge_variables = instance.merge_variables or {}
        return {
            **merge_variables,
            "data": merge_variables,
            "settings": self.settings_for(instance),
            "instance": instance,
            "size": size,
            "trmnl": trmnl,
        }

    def render(self, instance, size, trmnl):
        return render_to_string(self.template_name, self.get_context(instance, size, trmnl))

    def title(self, instance):
        return instance.name


class Registry:
    def __init__(self):
        self._plugins = {}

    def register(self, plugin_class):
        plugin = plugin_class()
        if not plugin.key:
            raise ValueError(f"{plugin_class.__name__} needs a key")
        self._plugins[plugin.key] = plugin
        return plugin_class

    def get(self, key):
        try:
            return self._plugins[key]
        except KeyError:
            raise PluginError(f"Unknown plugin {key!r}") from None

    def choices(self):
        return [(plugin.key, plugin.name) for plugin in self._plugins.values()]

    def __contains__(self, key):
        return key in self._plugins

    def __iter__(self):
        return iter(self._plugins.values())


registry = Registry()

TEMPLATE_BLOCK = re.compile(
    r"{%-?\s*template\s+(\w+)\s*-?%}(.*?){%-?\s*endtemplate\s*-?%}", re.DOTALL
)


def render_markup(source, context, engine="django", shared=""):
    """Render user-supplied markup with Django templates or Liquid.

    Liquid follows trmnl.com: shared markup is prepended to every layout, and
    ``{% template name %}…{% endtemplate %}`` blocks in it become partials for
    ``{% render "name" %}``.
    """
    if engine == "liquid":
        try:
            import liquid
        except ImportError:
            raise PluginError("Liquid templates need python-liquid: pip install 'django-trmnl[liquid]'") from None
        partials = dict(TEMPLATE_BLOCK.findall(shared or ""))
        shared = TEMPLATE_BLOCK.sub("", shared or "")
        environment = liquid.Environment(loader=liquid.DictLoader(partials))
        variables = {key: value for key, value in context.items() if key != "instance"}
        return environment.from_string(shared + source).render(**variables)
    template = Engine.get_default().from_string((shared or "") + source)
    return template.render(Context(context))


def fetch_json(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": "django-trmnl", **(headers or {})})
    with urllib.request.urlopen(request, timeout=conf.get("HTTP_TIMEOUT")) as response:
        return json.load(response)

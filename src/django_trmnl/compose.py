"""Build the full HTML document for a dashboard.

Plugins supply only ``layout`` (+ optional ``title_bar``) markup. On a custom
stack we own the rest of the hierarchy, Screen → (Mashup →) View, so this
module adds it, using the device profile's screen classes.
"""

import logging

from django.template.loader import render_to_string
from django.utils.html import escape
from django.utils.safestring import mark_safe

from . import conf, layouts
from .devices import get_profile

logger = logging.getLogger(__name__)

EMPTY_SLOT = '<div class="layout layout--col layout--center"><span class="label label--gray">Empty</span></div>'


def screen_classes(dashboard, profile, orientation):
    classes = ["screen", *profile.classes(orientation)]
    if dashboard.backdrop:
        classes.append("screen--backdrop")
    if dashboard.dark_mode:
        classes.append("screen--dark-mode")
    if dashboard.no_bleed:
        classes.append("screen--no-bleed")
    classes.extend(dashboard.extra_screen_classes.split())
    return " ".join(classes)


def render_cell(cell, size, profile, orientation, dom_id):
    instance = cell.instance
    plugin = instance.get_plugin()
    width, height = profile.css_size(orientation)
    trmnl = {
        "device": {
            "profile": profile.key,
            "width": width,
            "height": height,
            "bit_depth": profile.bit_depth,
            "orientation": orientation,
        },
        "plugin": {"key": plugin.key, "name": instance.name, "uuid": str(instance.uuid)},
        "size": size,
        "dom_id": dom_id,
    }
    try:
        markup = plugin.render(instance, size, trmnl)
    except Exception as error:
        logger.exception("Plugin %s failed to render instance %s", plugin.key, instance.pk)
        markup = (
            '<div class="layout layout--col layout--center gap--small">'
            '<span class="label label--error">Render error</span>'
            f'<span class="description" data-clamp="3">{escape(error)}</span></div>'
        )
    if cell.show_title_bar and 'class="title_bar' not in markup:
        markup += render_to_string(
            "django_trmnl/partials/title_bar.html",
            {"icon": plugin.icon, "title": plugin.title(instance), "instance_label": ""},
        )
    return mark_safe(markup)


def build_cells(dashboard, profile, orientation):
    cells = list(dashboard.ordered_cells())
    built = []
    if dashboard.is_fluid:
        for index, cell in enumerate(cells, start=1):
            size = cell.view_size()
            dom_id = f"cell-{index}"
            built.append(
                {
                    "size": size,
                    "dom_id": dom_id,
                    "cell_classes": " ".join(["mashup-cell", *cell.placement.classes]),
                    "markup": render_cell(cell, size, profile, orientation, dom_id),
                }
            )
        return built

    slots = layouts.FIXED_LAYOUTS[dashboard.layout]
    by_position = {cell.position: cell for cell in cells}
    for position, size in enumerate(slots, start=1):
        dom_id = f"cell-{position}"
        cell = by_position.get(position)
        markup = render_cell(cell, size, profile, orientation, dom_id) if cell else mark_safe(EMPTY_SLOT)
        built.append({"size": size, "dom_id": dom_id, "cell_classes": "", "markup": markup})
    return built


def compose(dashboard, profile_key="og", orientation="landscape"):
    profile = get_profile(profile_key)
    width, height = profile.image_size(orientation)
    if dashboard.is_fluid:
        mashup_classes = "mashup mashup--3x3"
    elif dashboard.layout == "1x1":
        mashup_classes = ""  # a full view sits directly inside the screen
    else:
        mashup_classes = f"mashup mashup--{dashboard.layout}"
    return render_to_string(
        "django_trmnl/screen.html",
        {
            "dashboard": dashboard,
            "width": width,
            "height": height,
            "css_url": conf.get("FRAMEWORK_CSS_URL"),
            "js_url": conf.get("FRAMEWORK_JS_URL"),
            "screen_classes": screen_classes(dashboard, profile, orientation),
            "screen_style": profile.style(),
            "mashup_classes": mashup_classes,
            "cells": build_cells(dashboard, profile, orientation),
        },
    )

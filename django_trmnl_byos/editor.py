"""Staff editor for dashboards and plugin instances, so nobody needs the Django admin."""

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import layouts
from .forms import (
    DashboardForm,
    MergeVariablesForm,
    PluginInstanceForm,
    clean_plugin_settings,
    make_cell_formset,
    settings_form,
)
from .models import Dashboard, PluginInstance
from .plugins import registry
from .services import refresh_instance
from .tasks import dashboards_for_instance, enqueue_render

# Where each slot sits in a layout's thumbnail: (column, row, column span, row span).
FIXED_THUMBNAILS = {
    "1x1": [(1, 1, 2, 2)],
    "1Lx1R": [(1, 1, 1, 2), (2, 1, 1, 2)],
    "1Tx1B": [(1, 1, 2, 1), (1, 2, 2, 1)],
    "1Lx2R": [(1, 1, 1, 2), (2, 1, 1, 1), (2, 2, 1, 1)],
    "2Lx1R": [(1, 1, 1, 1), (1, 2, 1, 1), (2, 1, 1, 2)],
    "2Tx1B": [(1, 1, 1, 1), (2, 1, 1, 1), (1, 2, 2, 1)],
    "1Tx2B": [(1, 1, 2, 1), (1, 2, 1, 1), (2, 2, 1, 1)],
    "2x2": [(1, 1, 1, 1), (2, 1, 1, 1), (1, 2, 1, 1), (2, 2, 1, 1)],
}


def layout_options(selected):
    options = []
    for value, label in layouts.LAYOUT_CHOICES:
        if value == layouts.FLUID:
            cells, grid = [(col, row, 1, 1) for row in (1, 2, 3) for col in (1, 2, 3)], 3
        else:
            cells, grid = FIXED_THUMBNAILS[value], 2
        options.append(
            {
                "value": value,
                "label": label,
                "grid": grid,
                "cells": [
                    {"number": number, "style": f"grid-column: {col} / span {cs}; grid-row: {row} / span {rs};"}
                    for number, (col, row, cs, rs) in enumerate(cells, start=1)
                ],
                "selected": value == selected,
            }
        )
    return options


@staff_member_required
def dashboard_edit(request, pk=None):
    dashboard = get_object_or_404(Dashboard, pk=pk) if pk else Dashboard(layout="1x1")
    if request.method == "POST":
        form = DashboardForm(request.POST, instance=dashboard)
        layout = request.POST.get("layout") or dashboard.layout
        cells = make_cell_formset(dashboard, request.POST, layout=layout)
        if form.is_valid() and cells.is_valid():
            with transaction.atomic():
                dashboard = form.save()
                cells.instance = dashboard
                cells.save()
                dashboard.touch()
            enqueue_render([dashboard.pk])
            messages.success(request, f"Saved {dashboard.name}.")
            return redirect("django_trmnl_byos:editor-dashboard", pk=dashboard.pk)
    else:
        form = DashboardForm(instance=dashboard)
        cells = make_cell_formset(dashboard)
    selected_layout = form["layout"].value() or dashboard.layout
    return render(
        request,
        "django_trmnl_byos/preview/dashboard_form.html",
        {
            "dashboard": dashboard,
            "form": form,
            "cells": cells,
            "layout_options": layout_options(selected_layout),
            "selected_layout": selected_layout,
            "fluid": selected_layout == layouts.FLUID,
            "slot_counts": {value: layouts.slot_count(value) for value, _ in layouts.LAYOUT_CHOICES},
            "has_plugins": PluginInstance.objects.exists(),
            "preview_url": reverse("django_trmnl_byos:preview-html", args=[dashboard.pk]) + "?profile=og"
            if dashboard.pk
            else "",
        },
    )


@staff_member_required
def dashboard_delete(request, pk):
    dashboard = get_object_or_404(Dashboard, pk=pk)
    if request.method == "POST":
        name = dashboard.name
        dashboard.delete()
        messages.success(request, f"Deleted {name}.")
        return redirect("django_trmnl_byos:preview-index")
    return render(
        request,
        "django_trmnl_byos/preview/confirm_delete.html",
        {
            "object": dashboard,
            "kind": "dashboard",
            "consequence": f"It will also be removed from {dashboard.playlist_items.count()} playlist item(s).",
            "cancel_url": reverse("django_trmnl_byos:editor-dashboard", args=[dashboard.pk]),
        },
    )


@staff_member_required
def plugin_list(request):
    instances = PluginInstance.objects.prefetch_related("cells__dashboard").order_by("name")
    for instance in instances:
        instance.plugin_name = registry.get(instance.plugin).name if instance.plugin in registry else instance.plugin
    return render(request, "django_trmnl_byos/preview/plugin_list.html", {"instances": instances})


@staff_member_required
def plugin_choose(request):
    return render(request, "django_trmnl_byos/preview/plugin_choose.html", {"plugins": list(registry)})


@staff_member_required
def plugin_edit(request, pk=None, plugin_key=None):
    if pk:
        instance = get_object_or_404(PluginInstance, pk=pk)
    else:
        if plugin_key not in registry:
            messages.error(request, "Pick a plugin type first.")
            return redirect("django_trmnl_byos:editor-plugin-choose")
        plugin = registry.get(plugin_key)
        instance = PluginInstance(plugin=plugin_key, name=plugin.name, settings={})
    plugin = instance.get_plugin()

    if request.method == "POST":
        options = settings_form(plugin, instance, request.POST)
        data_form = MergeVariablesForm(request.POST, prefix="data")
        valid = options.is_valid() and data_form.is_valid()
        if valid:
            try:
                # Set before validating the name form, whose model check reads the settings.
                instance.settings = clean_plugin_settings(plugin, options.cleaned_data)
            except forms.ValidationError as error:
                options.add_error(None, error)
                valid = False
        form = PluginInstanceForm(request.POST, instance=instance)
        valid = form.is_valid() and valid
        if valid:
            instance = form.save(commit=False)
            merge_variables = data_form.cleaned_data.get("merge_variables")
            if merge_variables is not None:
                instance.merge_variables = merge_variables
            instance.save()
            enqueue_render(dashboards_for_instance(instance))
            messages.success(request, f"Saved {instance.name}.")
            return redirect("django_trmnl_byos:editor-plugin", pk=instance.pk)
    else:
        form = PluginInstanceForm(instance=instance)
        options = settings_form(plugin, instance)
        data_form = MergeVariablesForm(initial={"merge_variables": instance.merge_variables}, prefix="data")

    return render(
        request,
        "django_trmnl_byos/preview/plugin_form.html",
        {
            "instance": instance,
            "plugin": plugin,
            "form": form,
            "options": options,
            "data_form": data_form,
            # Polling plugins fetch their own data; the rest are fed by hand or the webhook.
            "show_data": not plugin.polls,
            "webhook_url": request.build_absolute_uri(
                reverse("django_trmnl_byos:webhook", args=[instance.uuid])
            )
            if instance.pk
            else "",
            "dashboards": sorted({cell.dashboard for cell in instance.cells.select_related("dashboard")}, key=str)
            if instance.pk
            else [],
        },
    )


@staff_member_required
@require_POST
def plugin_refresh(request, pk):
    instance = get_object_or_404(PluginInstance, pk=pk)
    if not instance.get_plugin().polls:
        messages.error(request, f"{instance.name} doesn't fetch data on its own.")
    elif refresh_instance(instance):
        enqueue_render(dashboards_for_instance(instance))
        messages.success(request, f"Refreshed {instance.name}.")
    else:
        messages.error(request, f"Refresh failed: {instance.last_error}")
    return redirect("django_trmnl_byos:editor-plugin", pk=instance.pk)


@staff_member_required
def plugin_delete(request, pk):
    instance = get_object_or_404(PluginInstance, pk=pk)
    dashboards = sorted({cell.dashboard.name for cell in instance.cells.select_related("dashboard")})
    if request.method == "POST":
        name = instance.name
        affected = dashboards_for_instance(instance)
        instance.delete()
        enqueue_render(affected)
        messages.success(request, f"Deleted {name}.")
        return redirect("django_trmnl_byos:editor-plugins")
    return render(
        request,
        "django_trmnl_byos/preview/confirm_delete.html",
        {
            "object": instance,
            "kind": "plugin",
            "consequence": f"It will be removed from: {', '.join(dashboards)}." if dashboards else "",
            "cancel_url": reverse("django_trmnl_byos:editor-plugin", args=[instance.pk]),
        },
    )

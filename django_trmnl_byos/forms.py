"""Forms for the staff editor: dashboards (with their cells) and plugin instances."""

from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet, inlineformset_factory

from . import layouts
from .models import Dashboard, DashboardCell, PluginInstance
from .plugins import PluginError

MAX_CELLS = 9
TEXTAREA_SETTINGS = {"markup", "shared", "message"}


class DashboardForm(forms.ModelForm):
    class Meta:
        model = Dashboard
        fields = ["name", "layout", "backdrop", "dark_mode", "no_bleed", "extra_screen_classes"]
        widgets = {"layout": forms.RadioSelect}


class CellFormSet(BaseInlineFormSet):
    """Cells for one dashboard: slot numbers for fixed layouts, grid placement for fluid ones."""

    def __init__(self, *args, layout=None, **kwargs):
        self.layout = layout
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.fields["instance"].queryset = PluginInstance.objects.order_by("name")
            form.fields["instance"].label = "Plugin"
            form.fields["instance"].required = False

    def clean(self):
        super().clean()
        layout = self.layout or self.instance.layout
        used = []
        for form in self.forms:
            data = getattr(form, "cleaned_data", None) or {}
            if data.get("DELETE") or not data.get("instance"):
                continue
            used.append((form, data))

        if layout == layouts.FLUID:
            placements = []
            for form, data in used:
                placement = layouts.Placement(data["col"], data["row"], data["col_span"], data["row_span"])
                errors = layouts.placement_errors(placement)
                if errors:
                    form.add_error(None, "; ".join(errors))
                placements.append((str(data["instance"]), placement))
            for error in layouts.overlap_errors(placements):
                raise ValidationError(error)
        else:
            slots = layouts.slot_count(layout)
            taken = {}
            for form, data in used:
                position = data["position"]
                if not 1 <= position <= slots:
                    form.add_error("position", f"The {layout} layout has slots 1 to {slots}.")
                elif position in taken:
                    form.add_error("position", f"Slot {position} is already used by {taken[position]}.")
                else:
                    taken[position] = data["instance"]

    def _should_delete_form(self, form):
        # Clearing an existing cell's plugin removes that cell.
        cleared = bool(form.instance.pk) and not (getattr(form, "cleaned_data", None) or {}).get("instance")
        return super()._should_delete_form(form) or cleared


class CellForm(forms.ModelForm):
    class Meta:
        model = DashboardCell
        fields = ["instance", "position", "col", "row", "col_span", "row_span", "show_title_bar"]
        widgets = {
            name: forms.Select(choices=[(value, value) for value in range(1, choices + 1)])
            for name, choices in (
                ("position", MAX_CELLS),
                ("col", 3),
                ("row", 3),
                ("col_span", 3),
                ("row_span", 3),
            )
        }

    def has_changed(self):
        # A blank "add a cell" row only counts once a plugin is picked.
        if not self.instance.pk:
            return bool(self.data.get(self.add_prefix("instance")))
        return super().has_changed()


def cell_formset_class(extra):
    return inlineformset_factory(
        Dashboard,
        DashboardCell,
        form=CellForm,
        formset=CellFormSet,
        extra=extra,
        can_delete=True,
        max_num=MAX_CELLS,
    )


def make_cell_formset(dashboard, data=None, layout=None):
    existing = dashboard.cells.count() if dashboard.pk else 0
    formset_class = cell_formset_class(extra=max(0, MAX_CELLS - existing))
    return formset_class(
        data,
        instance=dashboard,
        prefix="cells",
        layout=layout,
        queryset=DashboardCell.objects.filter(dashboard=dashboard).order_by("position", "row", "col", "pk")
        if dashboard.pk
        else DashboardCell.objects.none(),
    )


class PluginInstanceForm(forms.ModelForm):
    class Meta:
        model = PluginInstance
        fields = ["name", "refresh_interval"]
        help_texts = {"refresh_interval": "Minutes between data refreshes and re-renders."}

    def _update_errors(self, errors):
        # The model re-checks settings in clean(); the settings form already shows those errors.
        if hasattr(errors, "error_dict"):
            errors.error_dict.pop("settings", None)
            if not errors.error_dict:
                return
        super()._update_errors(errors)


def _label(key):
    return key.replace("_", " ").capitalize()


def settings_form_class(plugin, current=None):
    """A form with one field per setting, typed from the plugin's defaults (or current values)."""
    current = current or {}
    fields = {}
    for key, default in plugin.default_settings.items():
        value = current.get(key, default)
        sample = default if default is not None else value
        help_text = plugin.help.get(key, "")
        options = {"label": _label(key), "help_text": help_text, "required": False}
        if key in plugin.choices:
            field = forms.ChoiceField(choices=[(choice, choice) for choice in plugin.choices[key]], **options)
        elif isinstance(sample, bool):
            field = forms.BooleanField(**options)
        elif isinstance(sample, int):
            field = forms.IntegerField(**options)
        elif isinstance(sample, float) or key in {"latitude", "longitude"}:
            field = forms.FloatField(**options)
        elif isinstance(sample, (list, dict)) or isinstance(value, (list, dict)):
            field = forms.JSONField(widget=forms.Textarea(attrs={"rows": 4}), **options)
        elif key in TEXTAREA_SETTINGS:
            field = forms.CharField(widget=forms.Textarea(attrs={"rows": 8}), strip=False, **options)
        else:
            field = forms.CharField(**options)
        fields[key] = field
    return type(f"{plugin.key.title()}SettingsForm", (forms.Form,), fields)


def settings_form(plugin, instance=None, data=None):
    current = (instance.settings if instance else None) or {}
    initial = {**plugin.default_settings, **current}
    return settings_form_class(plugin, current)(data, initial=initial, prefix="settings")


def clean_plugin_settings(plugin, cleaned):
    """Merge form values over the defaults and run the plugin's own validation."""
    values = {}
    for key, default in plugin.default_settings.items():
        value = cleaned.get(key)
        if isinstance(default, bool):
            value = bool(value)
        elif value is None or value == "":
            # Blank text stays blank (it often means "use the default behavior");
            # blank numbers and lists fall back to the plugin's default.
            value = "" if isinstance(default, str) else default
        values[key] = value
    try:
        plugin.clean_settings(values)
    except PluginError as error:
        raise ValidationError(str(error)) from None
    return values


class MergeVariablesForm(forms.Form):
    merge_variables = forms.JSONField(
        required=False,
        label="Data (merge variables)",
        help_text="What the templates render. Also set by the webhook.",
        widget=forms.Textarea(attrs={"rows": 6}),
    )

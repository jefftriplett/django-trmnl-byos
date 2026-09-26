import pytest

from django_trmnl_byos.compose import compose
from django_trmnl_byos.models import Dashboard, DashboardCell

pytestmark = pytest.mark.django_db


def test_single_view_sits_directly_in_screen(single):
    html = compose(single, "og")
    assert 'class="screen screen--og screen--md screen--1bit"' in html
    assert "mashup" not in html
    assert 'class="view view--full"' in html
    assert 'class="title_bar"' in html
    assert "Hello &lt;world&gt;" in html  # merge data is escaped
    assert "https://trmnl.com/css/3.3.2/plugins.min.css" in html


def test_fluid_mashup_markup(fluid):
    html = compose(fluid, "x", "portrait")
    assert "screen--v2 screen--lg screen--density-2x screen--4bit screen--portrait" in html
    assert 'class="mashup mashup--3x3"' in html
    assert (
        'class="mashup-cell mashup-cell--col-1 mashup-cell--col-span-2 mashup-cell--row-1 mashup-cell--row-span-3"'
        in html
    )
    assert 'class="view view--full" id="cell-1"' in html
    assert 'class="view view--quadrant" id="cell-2"' in html
    assert 'content="width=1404' in html  # portrait X: physical pixels, swapped


def test_fixed_mashup_fills_empty_slots(message):
    dashboard = Dashboard.objects.create(name="2x2", layout="2x2", backdrop=True, extra_screen_classes="screen--scale-xxsmall")
    DashboardCell.objects.create(dashboard=dashboard, instance=message, position=2)
    html = compose(dashboard, "og")
    assert 'class="mashup mashup--2x2"' in html
    assert html.count('class="view view--quadrant"') == 4
    assert html.count(">Empty<") == 3
    assert "screen--backdrop" in html and "screen--scale-xxsmall" in html


def test_title_bar_can_be_hidden(message):
    dashboard = Dashboard.objects.create(name="Bare", layout="1x1")
    DashboardCell.objects.create(dashboard=dashboard, instance=message, show_title_bar=False)
    assert "title_bar" not in compose(dashboard, "og")


def test_plugin_errors_render_in_the_cell():
    from django_trmnl_byos.models import PluginInstance

    broken = PluginInstance.objects.create(
        name="Broken", plugin="markup", settings={"markup": "{% if %}"}
    )
    dashboard = Dashboard.objects.create(name="Oops", layout="1x1")
    DashboardCell.objects.create(dashboard=dashboard, instance=broken)
    html = compose(dashboard, "og")
    assert "Render error" in html

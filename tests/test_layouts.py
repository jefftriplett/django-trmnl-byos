import pytest
from django.core.exceptions import ValidationError

from django_trmnl import layouts
from django_trmnl.layouts import Placement
from django_trmnl.models import Dashboard, DashboardCell


@pytest.mark.parametrize(
    "placement, size",
    [
        (Placement(1, 1, 3, 3), "full"),
        (Placement(1, 1, 2, 2), "full"),
        (Placement(1, 1, 3, 1), "half_horizontal"),
        (Placement(1, 1, 2, 1), "half_horizontal"),
        (Placement(3, 1, 1, 3), "half_vertical"),
        (Placement(3, 1, 1, 1), "quadrant"),
    ],
)
def test_view_size_for_placement(placement, size):
    assert layouts.view_size_for_placement(placement) == size


def test_placement_must_stay_inside_grid():
    assert layouts.placement_errors(Placement(1, 1, 3, 3)) == []
    assert layouts.placement_errors(Placement(2, 1, 3, 1)) == ["column 2 + span 3 runs past the 3x3 grid"]
    assert "row must be between 1 and 3" in layouts.placement_errors(Placement(1, 4, 1, 1))


def test_overlap_errors():
    ok = [("a", Placement(1, 1, 2, 3)), ("b", Placement(3, 1, 1, 3))]
    assert layouts.overlap_errors(ok) == []
    clash = [("a", Placement(1, 1, 2, 2)), ("b", Placement(2, 2, 2, 1))]
    assert layouts.overlap_errors(clash) == ["b overlaps a at column 2, row 2"]


def test_placement_classes():
    assert Placement(1, 2, 3, 1).classes == [
        "mashup-cell--col-1",
        "mashup-cell--col-span-3",
        "mashup-cell--row-2",
        "mashup-cell--row-span-1",
    ]


@pytest.mark.django_db
def test_cell_clean_checks_layout(message):
    fluid = Dashboard.objects.create(name="F", layout="3x3")
    with pytest.raises(ValidationError):
        DashboardCell(dashboard=fluid, instance=message, col=3, col_span=2).clean()
    fixed = Dashboard.objects.create(name="Split", layout="1Lx1R")
    with pytest.raises(ValidationError):
        DashboardCell(dashboard=fixed, instance=message, position=3).clean()
    DashboardCell(dashboard=fixed, instance=message, position=2).clean()


@pytest.mark.django_db
def test_fixed_cell_view_size(message):
    dashboard = Dashboard.objects.create(name="1Lx2R", layout="1Lx2R")
    sizes = [
        DashboardCell(dashboard=dashboard, instance=message, position=position).view_size()
        for position in (1, 2, 3)
    ]
    assert sizes == ["half_vertical", "quadrant", "quadrant"]

"""Mashup layouts: the eight fixed layouts plus the fluid ``3x3`` grid.

Framework reference: https://trmnl.com/framework/docs/3.3/mashup
"""

from dataclasses import dataclass

FULL = "full"
HALF_HORIZONTAL = "half_horizontal"
HALF_VERTICAL = "half_vertical"
QUADRANT = "quadrant"
VIEW_SIZES = [FULL, HALF_HORIZONTAL, HALF_VERTICAL, QUADRANT]

FLUID = "3x3"

# Fixed layouts list the view size of each slot, in markup order.
FIXED_LAYOUTS = {
    "1x1": [FULL],
    "1Lx1R": [HALF_VERTICAL, HALF_VERTICAL],
    "1Tx1B": [HALF_HORIZONTAL, HALF_HORIZONTAL],
    "1Lx2R": [HALF_VERTICAL, QUADRANT, QUADRANT],
    "2Lx1R": [QUADRANT, QUADRANT, HALF_VERTICAL],
    "2Tx1B": [QUADRANT, QUADRANT, HALF_HORIZONTAL],
    "1Tx2B": [HALF_HORIZONTAL, QUADRANT, QUADRANT],
    "2x2": [QUADRANT, QUADRANT, QUADRANT, QUADRANT],
}

LAYOUT_CHOICES = [
    ("1x1", "Full screen"),
    ("1Lx1R", "1 left, 1 right"),
    ("1Tx1B", "1 top, 1 bottom"),
    ("1Lx2R", "1 left, 2 right"),
    ("2Lx1R", "2 left, 1 right"),
    ("2Tx1B", "2 top, 1 bottom"),
    ("1Tx2B", "1 top, 2 bottom"),
    ("2x2", "2 x 2 grid"),
    (FLUID, "Fluid (3 x 3 grid)"),
]


@dataclass(frozen=True)
class Placement:
    col: int
    row: int
    col_span: int
    row_span: int

    def cells(self):
        return {
            (col, row)
            for col in range(self.col, self.col + self.col_span)
            for row in range(self.row, self.row + self.row_span)
        }

    @property
    def classes(self):
        return [
            f"mashup-cell--col-{self.col}",
            f"mashup-cell--col-span-{self.col_span}",
            f"mashup-cell--row-{self.row}",
            f"mashup-cell--row-span-{self.row_span}",
        ]


def placement_errors(placement):
    """Problems with one fluid cell. Start + span must stay within the 3x3 grid."""
    errors = []
    for axis, start, span in (
        ("column", placement.col, placement.col_span),
        ("row", placement.row, placement.row_span),
    ):
        if not 1 <= start <= 3:
            errors.append(f"{axis} must be between 1 and 3")
        if not 1 <= span <= 3:
            errors.append(f"{axis} span must be between 1 and 3")
        if start + span > 4:
            errors.append(f"{axis} {start} + span {span} runs past the 3x3 grid")
    return errors


def overlap_errors(placements):
    """Problems across a set of named fluid cells: ``[(label, Placement), ...]``."""
    errors = []
    taken = {}
    for label, placement in placements:
        for cell in sorted(placement.cells()):
            if cell in taken:
                errors.append(f"{label} overlaps {taken[cell]} at column {cell[0]}, row {cell[1]}")
                break
            taken[cell] = label
    return errors


def view_size_for_placement(placement):
    """Pick which of a plugin's four templates suits a fluid cell.

    In a fluid mashup the view always fills its cell; the view size only chooses
    the template: big blocks get ``full``, wide cells ``half_horizontal``, tall
    cells ``half_vertical`` and single tiles ``quadrant``.
    """
    if placement.col_span >= 2 and placement.row_span >= 2:
        return FULL
    if placement.col_span > placement.row_span:
        return HALF_HORIZONTAL
    if placement.row_span > placement.col_span:
        return HALF_VERTICAL
    return QUADRANT


def slot_count(layout):
    if layout == FLUID:
        return 9
    return len(FIXED_LAYOUTS[layout])

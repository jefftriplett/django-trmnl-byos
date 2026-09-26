from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ...devices import PROFILES
from ...models import Dashboard
from ...rendering import Renderer


class Command(BaseCommand):
    help = "Render dashboards now, optionally saving the image to a file."

    def add_arguments(self, parser):
        parser.add_argument("dashboard", nargs="*", type=int, help="Dashboard ids (default: all).")
        parser.add_argument("--profile", choices=sorted(PROFILES), default="og")
        parser.add_argument("--orientation", choices=["landscape", "portrait"], default="landscape")
        parser.add_argument("--output", help="Directory to also write image files into.")

    def handle(self, *args, dashboard, profile, orientation, output, **options):
        dashboards = Dashboard.objects.all()
        if dashboard:
            dashboards = dashboards.filter(pk__in=dashboard)
        if not dashboards:
            raise CommandError("No dashboards to render.")
        with Renderer() as renderer:
            for board in dashboards:
                render = renderer.render(board, profile, orientation)
                line = f"{board.pk} {board.name}: {render.duration_ms} ms, {len(render.image)} bytes"
                if output:
                    path = Path(output) / f"dashboard-{board.pk}-{profile}-{orientation}.{render.extension}"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(render.image)
                    line += f" → {path}"
                self.stdout.write(line)

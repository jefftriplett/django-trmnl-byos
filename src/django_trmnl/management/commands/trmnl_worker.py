import time

from django.core.management.base import BaseCommand

from ...rendering import Renderer
from ...services import run_once


class Command(BaseCommand):
    help = "Refresh plugin data and render stale dashboards for every device, in a loop."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one pass and exit.")
        parser.add_argument("--interval", type=int, default=30, help="Seconds between passes.")
        parser.add_argument("--force", action="store_true", help="Re-render even when nothing changed.")

    def handle(self, *args, once=False, interval=30, force=False, **options):
        with Renderer() as renderer:
            while True:
                started = time.monotonic()
                stats = run_once(renderer, force=force)
                elapsed = time.monotonic() - started
                if once or any(stats[key] for key in ("refreshed", "rendered", "failed")):
                    self.stdout.write(
                        f"{stats['targets']} targets · {stats['refreshed']} refreshed · "
                        f"{stats['rendered']} rendered · {stats['failed']} failed ({elapsed:.1f}s)"
                    )
                if once:
                    return
                time.sleep(max(1, interval - elapsed))

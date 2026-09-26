from django.core.management.base import BaseCommand
from django.db import transaction

from ...models import Dashboard, DashboardCell, Device, Playlist, PlaylistItem, PluginInstance

LIQUID_MARKUP = {
    "full": """<div class="layout layout--col gap--medium">
  <span class="title">{{ headline | default: "Waiting for data" }}</span>
  <div class="columns"><div class="column" data-overflow-max-cols="2">
    {% for task in tasks %}{% render "task", task: task %}{% endfor %}
  </div></div>
</div>""",
    "quadrant": """<div class="layout layout--col layout--center">
  <span class="value value--xxlarge">{{ tasks | size }}</span>
  <span class="label">open tasks</span>
</div>""",
}
LIQUID_SHARED = """{% template task %}<div class="item"><div class="meta"></div><div class="content">
  <span class="title title--small">{{ task.name }}</span>
  <span class="description">{{ task.due }}</span>
</div></div>{% endtemplate %}"""


class Command(BaseCommand):
    help = "Create demo plugins, dashboards (including a fluid 3x3 mashup), a playlist and a fake device."

    def add_arguments(self, parser):
        parser.add_argument("--no-device", action="store_true", help="Skip the simulated device.")

    @transaction.atomic
    def handle(self, *args, no_device=False, **options):
        clock, _ = PluginInstance.objects.get_or_create(
            name="Clock", plugin="clock", defaults={"settings": {"hour_format": "12"}, "refresh_interval": 5}
        )
        calendar, _ = PluginInstance.objects.get_or_create(
            name="Calendar", plugin="month_calendar", defaults={"settings": {"first_weekday": 6}}
        )
        weather, _ = PluginInstance.objects.get_or_create(
            name="Weather",
            plugin="weather",
            defaults={
                "settings": {"latitude": 33.749, "longitude": -84.388, "location": "Atlanta", "units": "fahrenheit"},
                "refresh_interval": 30,
            },
        )
        message, _ = PluginInstance.objects.get_or_create(
            name="Quote",
            plugin="message",
            defaults={
                "settings": {
                    "title": "Today",
                    "message": "Simple things should be simple, complex things should be possible.",
                    "author": "Alan Kay",
                }
            },
        )
        tasks, _ = PluginInstance.objects.get_or_create(
            name="Tasks (webhook, Liquid)",
            plugin="markup",
            defaults={
                "settings": {"engine": "liquid", "markup": LIQUID_MARKUP, "shared": LIQUID_SHARED},
                "merge_variables": {
                    "headline": "This week",
                    "tasks": [
                        {"name": "Ship django-trmnl", "due": "Friday"},
                        {"name": "Flash the TRMNL X", "due": "Saturday"},
                        {"name": "Water the plants", "due": "Daily"},
                    ],
                },
            },
        )

        fluid, created = Dashboard.objects.get_or_create(name="Fluid mashup", defaults={"layout": "3x3"})
        if created:
            # Calendar 2x2 feature, clock top right, weather down the right, quote along the bottom.
            DashboardCell.objects.create(dashboard=fluid, instance=calendar, col=1, row=1, col_span=2, row_span=2)
            DashboardCell.objects.create(dashboard=fluid, instance=clock, col=3, row=1)
            DashboardCell.objects.create(dashboard=fluid, instance=weather, col=3, row=2, row_span=2)
            DashboardCell.objects.create(dashboard=fluid, instance=message, col=1, row=3, col_span=2)

        split, created = Dashboard.objects.get_or_create(name="Tasks + clock", defaults={"layout": "1Lx2R"})
        if created:
            DashboardCell.objects.create(dashboard=split, instance=tasks, position=1)
            DashboardCell.objects.create(dashboard=split, instance=clock, position=2)
            DashboardCell.objects.create(dashboard=split, instance=weather, position=3)

        quote, created = Dashboard.objects.get_or_create(name="Quote", defaults={"layout": "1x1"})
        if created:
            DashboardCell.objects.create(dashboard=quote, instance=message, position=1)

        playlist, created = Playlist.objects.get_or_create(name="Demo", defaults={"is_default": True})
        if created:
            for order, dashboard in enumerate([fluid, split, quote]):
                PlaylistItem.objects.create(playlist=playlist, dashboard=dashboard, order=order)

        self.stdout.write(self.style.SUCCESS("Demo plugins, dashboards and playlist are ready."))
        self.stdout.write(f"Webhook for the Liquid tasks plugin: /api/custom_plugins/{tasks.uuid}")

        if not no_device:
            device, _ = Device.objects.get_or_create(
                mac_address="AA:BB:CC:00:00:01",
                defaults={"name": "Simulated TRMNL X", "profile": "x", "playlist": playlist},
            )
            self.stdout.write("Simulated device (TRMNL X profile):")
            self.stdout.write(f"  ID: {device.mac_address}")
            self.stdout.write(f"  Access-Token: {device.api_key}")

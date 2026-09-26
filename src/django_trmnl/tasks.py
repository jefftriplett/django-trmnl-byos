"""Background work, run by django-q2 (``manage.py qcluster``).

- ``tick`` runs every minute on a django-q2 Schedule: refresh plugin data that's
  due, then render every stale dashboard target.
- ``render_dashboard`` renders one dashboard for every device profile that shows
  it. It's queued when its data changes (webhook, admin) or a device asks for a
  dashboard that has no image yet.
"""

import logging

from django.apps import apps

logger = logging.getLogger(__name__)

TICK_NAME = "django-trmnl tick"


def tick():
    from .rendering import Renderer
    from .services import run_once

    with Renderer() as renderer:
        return run_once(renderer)


def render_dashboard(dashboard_id):
    from .models import Dashboard
    from .rendering import Renderer
    from .services import render_targets

    targets = [target for target in render_targets() if target[0] == dashboard_id]
    if not targets:
        return 0
    dashboard = Dashboard.objects.get(pk=dashboard_id)
    with Renderer() as renderer:
        for _, profile, orientation in targets:
            renderer.render(dashboard, profile, orientation)
    return len(targets)


def enqueue_render(dashboard_ids):
    """Queue renders when django-q2 is installed; otherwise the next tick covers it."""
    if not apps.is_installed("django_q"):
        return
    from django_q.tasks import async_task

    for dashboard_id in sorted(set(dashboard_ids)):
        async_task("django_trmnl.tasks.render_dashboard", dashboard_id, group="django-trmnl")


def dashboards_for_instance(instance):
    return list(instance.cells.values_list("dashboard_id", flat=True).distinct())


def ensure_schedule(**kwargs):
    """post_migrate hook: make sure the every-minute tick is scheduled."""
    if not apps.is_installed("django_q"):
        return
    from django_q.models import Schedule

    Schedule.objects.get_or_create(
        name=TICK_NAME,
        defaults={
            "func": "django_trmnl.tasks.tick",
            "schedule_type": Schedule.MINUTES,
            "minutes": 1,
            "repeats": -1,
        },
    )

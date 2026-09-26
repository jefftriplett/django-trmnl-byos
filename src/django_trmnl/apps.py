from django.apps import AppConfig


class DjangoTrmnlConfig(AppConfig):
    name = "django_trmnl"
    verbose_name = "TRMNL"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.db.models.signals import post_migrate

        from . import plugins  # noqa: F401  (registers the built-in plugins)
        from .tasks import ensure_schedule

        post_migrate.connect(ensure_schedule, sender=self)

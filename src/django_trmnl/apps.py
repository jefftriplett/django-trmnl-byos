from django.apps import AppConfig


class DjangoTrmnlConfig(AppConfig):
    name = "django_trmnl"
    verbose_name = "TRMNL"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import plugins  # noqa: F401  (registers the built-in plugins)

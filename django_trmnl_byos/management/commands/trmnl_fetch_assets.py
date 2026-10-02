import urllib.request

from django.core.management.base import BaseCommand, CommandError

from ... import assets, conf


class Command(BaseCommand):
    help = (
        "Download the pinned TRMNL Framework CSS, JS, fonts and plugin icons into ASSET_CACHE_DIR, "
        "so renders never fetch them from the network."
    )

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Download again even if a file is already cached.")

    def handle(self, *args, force, **options):
        css_url = conf.get("FRAMEWORK_CSS_URL")
        css = self.fetch(css_url, force)
        total = 0
        for url in assets.prefetch_urls(css.decode("utf-8", "replace")):
            body = self.fetch(url, force)
            total += len(body)
        self.stdout.write(self.style.SUCCESS(f"{total / 1_000_000:.1f} MB of framework assets in {assets.cache_dir()}"))

    def fetch(self, url, force):
        if not force and (body := assets.read(url)) is not None:
            self.stdout.write(f"  cached  {url}")
            return body
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "django-trmnl-byos"})
            with urllib.request.urlopen(request, timeout=max(conf.get("HTTP_TIMEOUT"), 60)) as response:
                body = response.read()
        except OSError as error:
            raise CommandError(f"Couldn't download {url}: {error}") from error
        assets.write(url, body)
        self.stdout.write(f"  fetched {url} ({len(body):,} bytes)")
        return body

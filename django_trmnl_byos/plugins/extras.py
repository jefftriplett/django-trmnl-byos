"""More built-in plugins: iCal agenda, RSS/Atom headlines, countdown, GitHub repos, website status."""

import datetime
import email.utils
import time
import urllib.error
import xml.etree.ElementTree as ET

from django.utils import timezone

from .base import Plugin, PluginError, fetch_bytes, fetch_json, registry
from .builtin import resolve_timezone


def _as_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


@registry.register
class ICalPlugin(Plugin):
    """Upcoming events from one or more iCal (.ics) feeds, repeating events included."""

    key = "ical"
    name = "Calendar agenda (iCal)"
    description = "Upcoming events from iCal links (Google, iCloud, Outlook, Fastmail, …)."
    template_name = "django_trmnl_byos/plugins/ical.html"
    polls = True
    default_settings = {"urls": [], "days": 7, "timezone": "", "show_location": True}
    help = {
        "urls": 'One .ics link, or a list of them. In Google Calendar: Settings → your calendar → "Secret address in iCal format".',
        "days": "How many days ahead to show, 1-60.",
        "timezone": 'Timezone for event times, e.g. "Central". Blank uses TIME_ZONE.',
        "show_location": "Show each event's location.",
    }

    def clean_settings(self, settings):
        urls = _as_list(settings.get("urls"))
        if not urls:
            raise PluginError("Add at least one .ics link in urls.")
        for url in urls:
            if not str(url).startswith(("http://", "https://", "webcal://")):
                raise PluginError(f"Not an http(s) or webcal link: {url!r}")
        try:
            days = int(settings.get("days", 7))
        except (TypeError, ValueError):
            raise PluginError("days must be a number.") from None
        if not 1 <= days <= 60:
            raise PluginError("days must be between 1 and 60.")
        resolve_timezone(settings.get("timezone", ""))

    def fetch(self, instance):
        import icalendar
        import recurring_ical_events

        settings = self.settings_for(instance)
        zone = resolve_timezone(settings.get("timezone", ""))
        start = datetime.datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=int(settings.get("days", 7)))
        events = []
        for url in _as_list(settings.get("urls")):
            url = str(url).replace("webcal://", "https://", 1)
            calendar = icalendar.Calendar.from_ical(fetch_bytes(url))
            for event in recurring_ical_events.of(calendar).between(start, end):
                begins = event.get("DTSTART").dt
                ends = event.get("DTEND").dt if event.get("DTEND") else begins
                all_day = not isinstance(begins, datetime.datetime)
                if not all_day:
                    begins = _aware(begins, zone).astimezone(zone)
                    ends = _aware(ends, zone).astimezone(zone)
                events.append(
                    {
                        "title": str(event.get("SUMMARY", "(no title)")),
                        "location": str(event.get("LOCATION", "")),
                        "all_day": all_day,
                        "start": begins.isoformat(),
                        "end": ends.isoformat(),
                    }
                )
        events.sort(key=lambda event: (event["start"][:10], not event["all_day"], event["start"]))
        return {"events": events[:100]}

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        zone = resolve_timezone(context["settings"].get("timezone", ""))
        now = datetime.datetime.now(zone)
        today = now.date()
        upcoming = []
        for event in (instance.merge_variables or {}).get("events", []):
            if event["all_day"]:
                day = datetime.date.fromisoformat(event["start"][:10])
                last_day = datetime.date.fromisoformat(event["end"][:10])
                if last_day <= today and last_day != day:
                    continue  # DTEND of an all-day event is exclusive
                when = "All day"
            else:
                begins = datetime.datetime.fromisoformat(event["start"]).astimezone(zone)
                if datetime.datetime.fromisoformat(event["end"]).astimezone(zone) < now:
                    continue
                day = begins.date()
                when = begins.strftime("%-I:%M %p")
            if day <= today:
                label = "Today"
            elif day == today + datetime.timedelta(days=1):
                label = "Tomorrow"
            else:
                label = day.strftime("%a %b %-d")
            upcoming.append({**event, "day_label": label, "when": when})
        context["upcoming"] = upcoming
        return context


def _aware(value, zone):
    if isinstance(value, datetime.datetime) and value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value


ATOM = "{http://www.w3.org/2005/Atom}"


@registry.register
class FeedPlugin(Plugin):
    """Latest headlines from an RSS or Atom feed."""

    key = "rss"
    name = "RSS feed"
    description = "Latest headlines from any RSS or Atom feed. Paste the feed's URL."
    template_name = "django_trmnl_byos/plugins/rss.html"
    polls = True
    default_settings = {"url": "", "limit": 8, "title": ""}
    help = {
        "url": "The feed's address, e.g. https://example.com/feed.xml (RSS 2.0 or Atom).",
        "limit": "How many items to keep, 1-30.",
        "title": "Heading to show; blank uses the feed's own title.",
    }

    def clean_settings(self, settings):
        if not str(settings.get("url", "")).startswith(("http://", "https://")):
            raise PluginError("Add the feed's URL (starting with http:// or https://).")
        try:
            limit = int(settings.get("limit", 8))
        except (TypeError, ValueError):
            raise PluginError("limit must be a number.") from None
        if not 1 <= limit <= 30:
            raise PluginError("limit must be between 1 and 30.")

    def fetch(self, instance):
        settings = self.settings_for(instance)
        try:
            root = ET.fromstring(fetch_bytes(settings["url"]))
        except ET.ParseError as error:
            raise PluginError(f"Couldn't read the feed: {error}") from None
        return parse_feed(root, int(settings.get("limit", 8)))

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        data = instance.merge_variables or {}
        context["heading"] = context["settings"].get("title") or data.get("feed_title") or instance.name
        items = []
        for item in data.get("items", []):
            published = item.get("published")
            label = ""
            if published:
                label = timezone.localtime(datetime.datetime.fromisoformat(published)).strftime("%b %-d")
            items.append({**item, "date_label": label})
        context["items"] = items
        return context


def _text(element, path, namespaces=None):
    found = element.find(path, namespaces or {})
    return (found.text or "").strip() if found is not None and found.text else ""


def _parse_date(value):
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)  # RSS: RFC 822
    except (TypeError, ValueError):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))  # Atom: RFC 3339
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.isoformat()


def parse_feed(root, limit):
    if root.tag == f"{ATOM}feed":
        feed_title = _text(root, f"{ATOM}title")
        entries = root.findall(f"{ATOM}entry")
        items = [
            {
                "title": _text(entry, f"{ATOM}title"),
                "link": (entry.find(f"{ATOM}link").get("href", "") if entry.find(f"{ATOM}link") is not None else ""),
                "published": _parse_date(_text(entry, f"{ATOM}published") or _text(entry, f"{ATOM}updated")),
            }
            for entry in entries
        ]
    else:
        channel = root.find("channel")
        if channel is None:
            raise PluginError("Not an RSS or Atom feed.")
        feed_title = _text(channel, "title")
        items = [
            {
                "title": _text(item, "title"),
                "link": _text(item, "link"),
                "published": _parse_date(_text(item, "pubDate")),
            }
            for item in channel.findall("item")
        ]
    return {"feed_title": feed_title, "items": [item for item in items if item["title"]][:limit]}


@registry.register
class CountdownPlugin(Plugin):
    """Days until the dates you care about. No network needed."""

    key = "countdown"
    name = "Countdown"
    description = "Days until upcoming dates: trips, launches, conferences, birthdays."
    template_name = "django_trmnl_byos/plugins/countdown.html"
    default_settings = {
        "events": [{"name": "New Year's Day", "date": "2027-01-01"}],
        "timezone": "",
        "show_past": False,
    }
    help = {
        "events": 'A list like [{"name": "DjangoCon US", "date": "2027-09-07"}] (dates as YYYY-MM-DD).',
        "timezone": 'Whose "today" to count from, e.g. "Central". Blank uses TIME_ZONE.',
        "show_past": "Keep showing dates that have passed, as days ago.",
    }

    def clean_settings(self, settings):
        events = settings.get("events")
        if not isinstance(events, list) or not events:
            raise PluginError('events must be a list like [{"name": "Launch", "date": "2027-01-01"}].')
        for event in events:
            if not isinstance(event, dict) or not event.get("name"):
                raise PluginError("Each event needs a name and a date.")
            try:
                datetime.date.fromisoformat(str(event.get("date")))
            except ValueError:
                raise PluginError(f"{event.get('name')}: date must be YYYY-MM-DD.") from None
        resolve_timezone(settings.get("timezone", ""))

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        settings = context["settings"]
        today = timezone.localdate(timezone=resolve_timezone(settings.get("timezone", "")))
        countdowns = []
        for event in settings.get("events") or []:
            try:
                day = datetime.date.fromisoformat(str(event.get("date")))
            except ValueError:
                continue
            days = (day - today).days
            if days < 0 and not settings.get("show_past"):
                continue
            countdowns.append({"name": event.get("name", ""), "date": day, "days": days, "abs_days": abs(days)})
        countdowns.sort(key=lambda countdown: (countdown["days"] < 0, abs(countdown["days"])))
        context["countdowns"] = countdowns
        return context


@registry.register
class GitHubReposPlugin(Plugin):
    """Stars, open issues, forks and the latest release for GitHub repositories."""

    key = "github_repos"
    name = "GitHub repos"
    description = "Stars, open issues/PRs, forks and latest release for a few repositories."
    template_name = "django_trmnl_byos/plugins/github_repos.html"
    polls = True
    default_settings = {"repos": [], "token": ""}
    help = {
        "repos": 'A list of "owner/name" repositories.',
        "token": "Optional GitHub token for private repos or higher rate limits (60 requests/hour without one).",
    }

    def clean_settings(self, settings):
        repos = _as_list(settings.get("repos"))
        if not repos:
            raise PluginError('Add at least one repository, like "owner/name".')
        for repo in repos:
            if str(repo).count("/") != 1:
                raise PluginError(f'{repo!r} should look like "owner/name".')

    def fetch(self, instance):
        settings = self.settings_for(instance)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if settings.get("token"):
            headers["Authorization"] = f"Bearer {settings['token']}"
        repos = []
        for name in _as_list(settings.get("repos")):
            data = fetch_json(f"https://api.github.com/repos/{name}", headers)
            release = None
            try:
                latest = fetch_json(f"https://api.github.com/repos/{name}/releases/latest", headers)
                release = {"tag": latest.get("tag_name", ""), "published": latest.get("published_at")}
            except urllib.error.HTTPError as error:
                if error.code != 404:  # 404 just means no releases yet
                    raise
            repos.append(
                {
                    "name": data.get("full_name", name),
                    "stars": data.get("stargazers_count", 0),
                    "forks": data.get("forks_count", 0),
                    "open_issues": data.get("open_issues_count", 0),  # includes pull requests
                    "pushed_at": data.get("pushed_at"),
                    "release": release,
                }
            )
        return {"repos": repos}


@registry.register
class WebsiteStatusPlugin(Plugin):
    """Up/down and response time for a list of websites."""

    key = "website_status"
    name = "Website status"
    description = "Checks websites on each refresh: up or down, and how fast they answered."
    template_name = "django_trmnl_byos/plugins/website_status.html"
    polls = True
    default_settings = {"sites": []}
    help = {
        "sites": 'A list of URLs, or of {"name": "Blog", "url": "https://…"} for a friendlier label.',
    }

    def clean_settings(self, settings):
        sites = _as_list(settings.get("sites"))
        if not sites:
            raise PluginError("Add at least one site.")
        for site in sites:
            url = site.get("url") if isinstance(site, dict) else site
            if not str(url or "").startswith(("http://", "https://")):
                raise PluginError(f"Not an http(s) URL: {url!r}")

    def fetch(self, instance):
        results = []
        for site in _as_list(self.settings_for(instance).get("sites")):
            url = site["url"] if isinstance(site, dict) else site
            name = site.get("name") if isinstance(site, dict) else None
            started = time.monotonic()
            try:
                fetch_bytes(url)
                status, error = 200, ""
            except urllib.error.HTTPError as http_error:
                status, error = http_error.code, f"HTTP {http_error.code}"
            except Exception as other:  # DNS, timeouts, TLS, refused connections…
                status, error = None, str(getattr(other, "reason", other))[:80]
            results.append(
                {
                    "name": name or url.split("//", 1)[-1].rstrip("/"),
                    "url": url,
                    "up": status is not None and status < 400,
                    "status": status,
                    "ms": round((time.monotonic() - started) * 1000),
                    "error": error,
                }
            )
        return {"sites": results, "checked_at": timezone.now().isoformat()}

    def get_context(self, instance, size, trmnl):
        context = super().get_context(instance, size, trmnl)
        sites = (instance.merge_variables or {}).get("sites", [])
        context["down_count"] = sum(1 for site in sites if not site["up"])
        checked = (instance.merge_variables or {}).get("checked_at")
        context["checked_label"] = (
            timezone.localtime(datetime.datetime.fromisoformat(checked)).strftime("%-I:%M %p") if checked else ""
        )
        return context

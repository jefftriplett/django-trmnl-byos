"""Django project settings for django-trmnl. Fine for a home network; review before exposing it anywhere.

Configured from the environment, following jefftriplett/django-startproject: compose.yml passes
.env (from .env-dist) to the containers; running locally without it uses SQLite.
"""

from pathlib import Path

from environs import env

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env.str("SECRET_KEY", default="django-insecure-change-me")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DJANGO_DEBUG", default=True)

# Devices on your LAN reach the server by IP, so allow any host by default.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
]

# Third-party apps

INSTALLED_APPS += [
    "django_browser_reload",
    "django_mcpz",
    "django_mcpz.oauth",
    "django_prodserver",
    "django_q",
    "django_tailwind_cli",
    "health_check",
]

# Our apps

INSTALLED_APPS += [
    "django_trmnl_byos",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_browser_reload.middleware.BrowserReloadMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
            "debug": DEBUG,
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: SQLite by default; compose.yml points DATABASE_URL at Postgres.

DATABASES = {
    "default": env.dj_db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = env.str("TIME_ZONE", default="America/Chicago")
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)

STATIC_ROOT = str(BASE_DIR.joinpath("staticfiles"))
STATIC_URL = "/static/"
# django-tailwind-cli builds the preview pages' CSS straight into the app's static
# directory, so the compiled file ships with the package and host projects need no Tailwind.
STATICFILES_DIRS = [BASE_DIR / "django_trmnl_byos" / "static"]


# Parse cache URLs, e.g. "redis://localhost:6379/0"
CACHES = {"default": env.dj_cache_url("CACHE_URL", default="locmem://")}

# Tailwind CSS settings

TAILWIND_CLI_AUTOMATIC_DOWNLOAD = env.bool("TAILWIND_CLI_AUTOMATIC_DOWNLOAD", default=True)
TAILWIND_CLI_DIST_CSS = "django_trmnl_byos/preview.css"
TAILWIND_CLI_SRC_CSS = "django_trmnl_byos/tailwind/preview.css"
TAILWIND_CLI_VERSION = env.str("TAILWIND_CLI_VERSION", default="4.3.3")

# Django-Q2 settings: the database is the broker (manage.py qcluster).
# One worker: renders are CPU-heavy, and SQLite allows a single writer.

Q_CLUSTER = {
    "catch_up": False,
    "max_attempts": 1,
    "name": "django-trmnl",
    "orm": "default",
    "queue_limit": 50,
    "recycle": 500,
    "retry": 360,
    "save_limit": 200,
    "timeout": 300,
    "workers": env.int("Q_WORKERS", default=1),
}

# Django Prodserver settings (manage.py server web, manage.py worker worker)

PRODUCTION_PROCESSES = {
    "web": {
        "BACKEND": "django_prodserver.backends.servers.gunicorn.GunicornServer",
        "ARGS": {"bind": "0.0.0.0:8000", "workers": "2", "access-logfile": "-"},
    },
    "worker": {
        "BACKEND": "django_prodserver.backends.workers.django_q2.DjangoQ2Worker",
        "ARGS": {},
    },
}

# django-trmnl settings

DJANGO_TRMNL_BYOS = {
    "RENDER_INLINE": env.bool("TRMNL_RENDER_INLINE", default=False),
    "BASE_URL": env.str("TRMNL_BASE_URL", default="") or None,
    "PLAYWRIGHT_WS_ENDPOINT": env.str("PLAYWRIGHT_WS_ENDPOINT", default="") or None,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django_trmnl_byos": {"handlers": ["console"], "level": "INFO"}},
}

# MCP server (django-mcpz, routed at /mcp by config.urls; see config/mcp.py)

MCP_ENABLED = env.bool("MCP_ENABLED", default=True)
# Optional shared secret: clients send `Authorization: Bearer <token>`. Empty turns
# the shared token off - it never opens the endpoint. Staff can always connect
# through OAuth instead (Claude Code, Claude.ai, ChatGPT).
# Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"
MCP_AUTH_TOKEN = env.str("MCP_AUTH_TOKEN", default="")

# The OAuth consent page asks staff to log in; this project's login is the admin's.
LOGIN_URL = "admin:login"

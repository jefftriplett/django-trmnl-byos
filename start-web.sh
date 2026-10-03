#!/bin/sh
uv run -m manage migrate --noinput

uv run -m manage collectstatic --noinput

# Starts the "web" entry in PRODUCTION_PROCESSES (django-prodserver). `exec`
# hands the container's stop signal to the server so it shuts down cleanly
# instead of being killed after the stop timeout.
exec uv run -m manage server web

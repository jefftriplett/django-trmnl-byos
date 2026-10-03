#!/bin/sh
# Serve Playwright over a websocket for the app's renders (django_trmnl_byos/rendering.py).
# Binds 0.0.0.0 so the app can reach it across the compose network. Never
# publish this port: whoever reaches it gets full control of a browser.
exec playwright run-server --port 3000 --host 0.0.0.0

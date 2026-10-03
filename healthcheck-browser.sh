#!/bin/sh
set -e

# /json/version answers "Running" once the server is listening. That proves
# the server is up, not that a browser can still launch.
curl -fsS http://localhost:3000/json/version | grep -q Running

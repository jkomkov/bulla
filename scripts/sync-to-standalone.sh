#!/bin/sh
set -eu

# Compatibility wrapper. The release tool is deliberately noninteractive and
# requires every path and branch expectation to be explicit.
exec python3 "$(dirname "$0")/sync_to_standalone.py" "$@"

#!/bin/sh
set -eu

mkdir -p "$HOME/workspace"

# `t3 serve` prints an admin pairing token and QR code to stdout on startup.
# Keep that output out of container logs; stderr remains available for errors.
exec /opt/t3-code/bin/t3 serve "$HOME/workspace" > /dev/null

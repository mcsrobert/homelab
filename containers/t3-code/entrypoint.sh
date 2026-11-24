#!/bin/sh
set -eu

mkdir -p "$HOME/workspace"

# Suppress stdout because t3 serve prints the admin pairing token there.
exec /opt/t3-code/bin/t3 serve "$HOME/workspace" > /dev/null

#!/bin/sh
# Use the image's Node.js even when a project pins another version with mise.
exec /usr/local/bin/node /opt/t3-code/npm/node_modules/@openai/codex/bin/codex.js "$@"

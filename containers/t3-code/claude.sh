#!/bin/sh
# Load the OAuth token from the mounted Secret only when Claude starts, so the
# server and the other agents it starts do not inherit it. Without the file,
# the environment passes through unchanged.
set -eu

token=/etc/claude-auth/CLAUDE_CODE_OAUTH_TOKEN
if [ -r "$token" ]; then
  CLAUDE_CODE_OAUTH_TOKEN=$(/bin/cat "$token")
  export CLAUDE_CODE_OAUTH_TOKEN
fi
exec /opt/t3-code/npm/node_modules/@anthropic-ai/claude-code-linux-arm64/claude "$@"

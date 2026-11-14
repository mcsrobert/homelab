#!/bin/sh
# Run the npm wrapper, which locates the native binary and its bundled tools,
# with the image's Node.js rather than a version a project selects with mise.
exec /usr/local/bin/node /opt/t3-code/npm/node_modules/@openai/codex/bin/codex.js "$@"

# shellcheck shell=sh
# /etc/profile resets PATH for login shells, so restore the image's PATH.
export PATH="/opt/t3-code/bin:/opt/t3-code/mise/shims:$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"

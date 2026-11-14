# shellcheck shell=sh
# /etc/profile resets PATH for login shells, so restore the image's PATH.
export PATH="/opt/t3-code/bin:$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"

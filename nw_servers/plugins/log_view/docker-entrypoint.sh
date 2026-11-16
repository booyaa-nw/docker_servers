#!/bin/sh
set -eu

MIB_DIR=${LOG_VIEW_MIB_DIR:-/config/mibs}
DEFAULT_MIB_DIR=/usr/local/share/netrig/default-mibs

mkdir -p "$MIB_DIR"
if [ ! -w "$MIB_DIR" ]; then
  echo "log-view: MIB directory '$MIB_DIR' is not writable by UID $(id -u); fix its ownership/permissions or set LOG_VIEW_UID/LOG_VIEW_GID to the directory owner." >&2
  exec "$@"
fi
for mib in "$DEFAULT_MIB_DIR"/*; do
  [ -f "$mib" ] || continue
  target="$MIB_DIR/${mib##*/}"
  if [ ! -e "$target" ]; then
    if ! cp "$mib" "$target"; then
      echo "log-view: could not copy standard MIB '$mib' to '$MIB_DIR'." >&2
      exec "$@"
    fi
  fi
done

exec "$@"

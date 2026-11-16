#!/bin/sh
set -eu

MIB_DIR=${LOG_VIEW_MIB_DIR:-/config/mibs}
DEFAULT_MIB_DIR=/usr/local/share/netrig/default-mibs

mkdir -p "$MIB_DIR"
for mib in "$DEFAULT_MIB_DIR"/*; do
  [ -f "$mib" ] || continue
  target="$MIB_DIR/${mib##*/}"
  if [ ! -e "$target" ]; then
    cp "$mib" "$target"
  fi
done

exec "$@"

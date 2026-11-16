#!/bin/sh
set -eu

MIB_DIR=${LOG_VIEW_MIB_DIR:-/config/mibs}
DEFAULT_MIB_DIR=/usr/local/share/netrig/default-mibs

mkdir -p "$MIB_DIR"
if [ -w "$MIB_DIR" ]; then
  for mib in "$DEFAULT_MIB_DIR"/*; do
    [ -f "$mib" ] || continue
    target="$MIB_DIR/${mib##*/}"
    if [ ! -e "$target" ] && ! cp "$mib" "$target"; then
      echo "log-view: could not copy standard MIB '$mib' to '$MIB_DIR'. Check its ownership and permissions." >&2
      break
    fi
  done
else
  echo "log-view: MIB directory '$MIB_DIR' is not writable by UID $(id -u). Check its ownership and permissions." >&2
fi

# Diagnose the bind-mounted database directory before Uvicorn starts so a
# permission error does not cause an opaque restart loop.
DATA_DIR=${LOG_VIEW_DATA_DIR:-/data}
if [ ! -d "$DATA_DIR" ] || [ ! -w "$DATA_DIR" ]; then
  echo "log-view: data directory '$DATA_DIR' is not writable by UID $(id -u). Check the host bind mount and LOG_VIEW_UID/LOG_VIEW_GID." >&2
fi

exec "$@"

#!/bin/sh
set -eu

bind_uid=${BIND_UID:-0}
bind_gid=${BIND_GID:-0}
case "$bind_uid:$bind_gid" in
  *[!0-9:]*|:*|*:) echo "BIND_UID/BIND_GID must be numeric" >&2; exit 2 ;;
esac

share_group=$(awk -F: -v id="$bind_gid" '$3 == id { print $1; exit }' /etc/group)
if [ -z "$share_group" ]; then
  share_group=netrigftp
  suffix=0
  while awk -F: -v name="$share_group" '$1 == name { found=1 } END { exit !found }' /etc/group; do
    suffix=$((suffix + 1))
    share_group="netrigftp_${suffix}"
  done
  groupadd -g "$bind_gid" "$share_group"
fi

if ! awk -F: '$1 == "netrigftp" { found=1 } END { exit !found }' /etc/passwd; then
  useradd --no-create-home --home-dir /srv/public --shell /usr/sbin/nologin \
    --uid 10001 --gid "$share_group" netrigftp
else
  usermod --gid "$share_group" --home /srv/public --shell /usr/sbin/nologin netrigftp
fi

inotifywait -m -r -e close_write,moved_to,create --format '%w%f' /srv/public 2>/dev/null |
  while IFS= read -r uploaded; do
    if [ -e "$uploaded" ]; then
      chown "$bind_uid:$bind_gid" "$uploaded" || true
    fi
  done &

exec /usr/sbin/in.tftpd --foreground --listen --user netrigftp --secure --create --permissive --umask 0002 /srv/public

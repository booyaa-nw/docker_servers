#!/bin/sh
set -eu

username=${FTP_USERNAME:-ftpuser}
password=${FTP_PASSWORD:-}
bind_uid=${BIND_UID:-0}
bind_gid=${BIND_GID:-0}
case "$username" in
  ''|*[!A-Za-z0-9_.-]*) echo "FTP_USERNAME may contain only letters, numbers, dots, underscores, and hyphens" >&2; exit 2 ;;
esac
if [ -z "$password" ]; then
  echo "FTP_PASSWORD must be set in .env" >&2
  exit 2
fi
case "$password" in
  *:*) echo "FTP_PASSWORD must not contain a colon" >&2; exit 2 ;;
esac
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

if ! awk -F: -v name="$username" '$1 == name { found=1 } END { exit !found }' /etc/passwd; then
  useradd -M -d /srv/public -s /sbin/nologin -u 10001 -g "$share_group" "$username"
else
  usermod -g "$share_group" -d /srv/public -s /sbin/nologin "$username"
fi
printf '%s:%s\n' "$username" "$password" | chpasswd

inotifywait -m -r -e close_write,moved_to,create --format '%w%f' /srv/public 2>/dev/null |
  while IFS= read -r uploaded; do
    if [ -e "$uploaded" ]; then
      chown "$bind_uid:$bind_gid" "$uploaded" || true
    fi
  done &

cp /etc/vsftpd/vsftpd.conf.template /tmp/vsftpd.conf
if [ -n "${FTP_PASV_ADDRESS:-}" ]; then
  case "$FTP_PASV_ADDRESS" in
    *[!A-Za-z0-9.-]*) echo "FTP_PASV_ADDRESS must be an IPv4 address or hostname" >&2; exit 2 ;;
  esac
  printf '\npasv_addr_resolve=YES\npasv_address=%s\n' "$FTP_PASV_ADDRESS" >> /tmp/vsftpd.conf
fi
exec /usr/sbin/vsftpd /tmp/vsftpd.conf

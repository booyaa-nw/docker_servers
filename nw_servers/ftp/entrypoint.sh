#!/bin/sh
set -eu

address=${FTP_PASV_ADDRESS:-127.0.0.1}
username=${FTP_USERNAME:-ftpuser}
password=${FTP_PASSWORD:-}
case "$address" in
  *[!A-Za-z0-9.-]*|'') echo "FTP_PASV_ADDRESS must be an IPv4 address or hostname" >&2; exit 2 ;;
esac
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

if ! awk -F: -v name="$username" '$1 == name { found=1 } END { exit !found }' /etc/passwd; then
  adduser -D -h /srv/public -s /sbin/nologin "$username"
fi
printf '%s:%s\n' "$username" "$password" | chpasswd

sed "s/@FTP_PASV_ADDRESS@/$address/g" /etc/vsftpd/vsftpd.conf.template > /tmp/vsftpd.conf
exec /usr/sbin/vsftpd /tmp/vsftpd.conf

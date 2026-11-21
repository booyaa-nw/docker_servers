#!/bin/sh
set -eu

address=${FTP_PASV_ADDRESS:-127.0.0.1}
case "$address" in
  *[!A-Za-z0-9.-]*|'') echo "FTP_PASV_ADDRESS must be an IPv4 address or hostname" >&2; exit 2 ;;
esac
sed "s/@FTP_PASV_ADDRESS@/$address/g" /etc/vsftpd/vsftpd.conf.template > /tmp/vsftpd.conf
exec /usr/sbin/vsftpd /tmp/vsftpd.conf

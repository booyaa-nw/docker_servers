FROM alpine:3.22

RUN apk add --no-cache shadow vsftpd
COPY vsftpd.conf /etc/vsftpd/vsftpd.conf.template
COPY pam-vsftpd /etc/pam.d/vsftpd
COPY entrypoint.sh /usr/local/bin/netrig-ftp-entrypoint
RUN chmod 755 /usr/local/bin/netrig-ftp-entrypoint \
    && mkdir -p /etc/pam.d /var/run/vsftpd/empty /srv/public

EXPOSE 2121/tcp 21100-21110/tcp
ENTRYPOINT ["/usr/local/bin/netrig-ftp-entrypoint"]

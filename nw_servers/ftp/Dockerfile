FROM alpine:3.22

RUN apk add --no-cache vsftpd
COPY vsftpd.conf /etc/vsftpd/vsftpd.conf.template
COPY entrypoint.sh /usr/local/bin/netrig-ftp-entrypoint
RUN chmod 755 /usr/local/bin/netrig-ftp-entrypoint \
    && mkdir -p /var/run/vsftpd/empty /srv/public

EXPOSE 2121/tcp 21100-21110/tcp
ENTRYPOINT ["/usr/local/bin/netrig-ftp-entrypoint"]

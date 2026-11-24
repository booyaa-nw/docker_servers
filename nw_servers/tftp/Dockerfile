FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends inotify-tools passwd tftpd-hpa \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /srv/public

EXPOSE 69/udp
COPY entrypoint.sh /usr/local/bin/netrig-tftp-entrypoint
RUN chmod 755 /usr/local/bin/netrig-tftp-entrypoint
ENTRYPOINT ["/usr/local/bin/netrig-tftp-entrypoint"]

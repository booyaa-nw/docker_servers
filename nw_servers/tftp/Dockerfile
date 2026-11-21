FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tftpd-hpa \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /srv/public

EXPOSE 69/udp
ENTRYPOINT ["/usr/sbin/in.tftpd", "--foreground", "--listen", "--user", "nobody", "--secure", "/srv/public"]

FROM python:3.15-slim AS standard-mibs

RUN sed -i 's/^Components: main$/Components: main non-free/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends snmp-mibs-downloader \
    && download-mibs \
    && mkdir -p /usr/local/share/netrig/default-mibs \
    && find /var/lib/mibs -type f -exec sh -c 'for source do name=${source##*/}; case "$name" in *.mib|*.txt|*.my) ;; *) name="$name.txt" ;; esac; cp "$source" "/usr/local/share/netrig/default-mibs/$name"; done' sh {} + \
    && test -n "$(find /usr/local/share/netrig/default-mibs -type f -print -quit)" \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY --from=standard-mibs /usr/local/share/netrig/default-mibs /usr/local/share/netrig/default-mibs
COPY config/mibs-bundled /app/config/mibs-bundled
COPY config/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh && mkdir -p /data /config/mibs /data/mibs-compiled

EXPOSE 8080 1514/udp 1514/tcp 1162/udp 1162/tcp
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

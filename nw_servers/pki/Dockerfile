FROM golang:1.25-bookworm AS builder
ENV CGO_ENABLED=0 GOTOOLCHAIN=auto
RUN go install github.com/cloudflare/cfssl/cmd/cfssl@v1.7.0

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /go/bin/cfssl /usr/local/bin/cfssl
COPY pki.py /app/pki.py
COPY tests /app/tests
# COPY can preserve restrictive host modes. All configured UIDs must read the CLI/tests.
RUN chmod 755 /app /app/tests \
    && chmod 644 /app/pki.py /app/tests/*.py
ENV PYTHONDONTWRITEBYTECODE=1 PKI_TEST_TMP=/tmp
USER 1000:1000
# Fail the build if the non-root runtime user cannot load the CLI.
RUN python3 /app/pki.py --help > /dev/null
ENTRYPOINT ["python3", "/app/pki.py"]
CMD ["--help"]

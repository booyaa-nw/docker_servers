FROM alpine:3.22
RUN apk add --no-cache openssl
ENTRYPOINT ["sh", "-c", "set -eu; umask 077; if [ ! -s /certs/tls.crt ] || [ ! -s /certs/tls.key ]; then openssl req -x509 -newkey rsa:3072 -nodes -keyout /certs/tls.key -out /certs/tls.crt -days 825 -subj \"/CN=${TLS_CERT_CN}\" -addext \"subjectAltName=${TLS_CERT_SAN}\"; fi"]

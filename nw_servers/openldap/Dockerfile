FROM alpine:3.20

RUN apk add --no-cache \
      openldap \
      openldap-clients \
      openldap-back-mdb \
      gettext \
      jq

COPY nwlab.schema /etc/openldap/schema/nwlab.schema
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 389
ENTRYPOINT ["/entrypoint.sh"]

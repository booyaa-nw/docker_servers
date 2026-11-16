FROM debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      freeradius freeradius-ldap freeradius-utils ca-certificates jq gettext-base \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/freeradius \
 && chown freerad:freerad /run/freeradius

# Dynamic VLAN: default / inner-tunnel の post-auth 先頭にポリシー呼び出しを差し込む
RUN set -e; R=/etc/freeradius/3.0; \
    sed -i '0,/^[[:space:]]*post-auth {/s//post-auth {\n\tnwlab_vlan_outer/' $R/sites-available/default; \
    sed -i '0,/^[[:space:]]*post-auth {/s//post-auth {\n\tnwlab_vlan_inner/' $R/sites-available/inner-tunnel; \
    grep -q nwlab_vlan_outer $R/sites-available/default; \
    grep -q nwlab_vlan_inner $R/sites-available/inner-tunnel
COPY config/nwlab_vlan_base /etc/freeradius/3.0/policy.d/nwlab_vlan_base
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 1812/udp 1813/udp

# 動作確認時は環境変数 RADIUS_DEBUG=1 で freeradius -X（デバッグ出力）になります
ENTRYPOINT ["/entrypoint.sh"]

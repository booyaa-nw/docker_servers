#!/bin/sh
# nw-openldap: 共有設定(settings.json)を監視し、変更があれば slapd を再起動する。
# ドメインごとに独立したMDBを使用する。
set -eu

SHARED=${SHARED_DIR:-/shared}
SETTINGS=$SHARED/settings.json
STATUS=$SHARED/status-ldap.json
IMPORT=$SHARED/import.ldif
TEMPLATES=${TEMPLATE_DIR:-/templates}
CONF=${SLAPD_CONF:-/etc/openldap/slapd.conf}

export SCHEMA_DIR=${SCHEMA_DIR:-/etc/openldap/schema}
export MODULE_DIR=${MODULE_DIR:-/usr/lib/openldap}
export RUN_DIR=${RUN_DIR:-/run/openldap}
DATA_ROOT=${DATA_DIR:-/var/lib/openldap/openldap-data}
SLAPD_USER=${SLAPD_USER:-ldap}
SLAPD_URLS=${SLAPD_URLS:-ldap:///}
LOCAL_URI=${LOCAL_URI:-ldap://127.0.0.1}

PID=""

log() { echo "[openldap] $*"; }
now() { date '+%Y-%m-%dT%H:%M:%S%z'; }
get() { jq -r "$1" "$SETTINGS"; }

umask 077
mkdir -p "$RUN_DIR" "$DATA_ROOT"
chown "$SLAPD_USER:$SLAPD_USER" "$RUN_DIR"

write_status() { # $1=generation $2=true/false $3=message
  jq -n --argjson g "$1" --argjson ok "$2" --arg m "$3" --arg t "$(now)" --arg b "$(get .base_dn)" \
    '{applied_generation:$g, ok:$ok, message:$m, base_dn:$b, time:$t}' > "$STATUS.tmp"
  mv "$STATUS.tmp" "$STATUS"
}

seed_settings() {
  [ -s "$SETTINGS" ] && return 0
  if [ -e "$DATA_ROOT/data.mdb" ]; then
    log "既存のMDBに対応するsettings.jsonがありません。元の設定を復元してください"
    exit 1
  fi
  : "${LDAP_BASE_DN:?}" "${LDAP_ADMIN_PASSWORD:?}" "${LDAP_RADIUS_PASSWORD:?}"
  log "settings.json がないため環境変数から作成します"
  jq -n --arg base "$LDAP_BASE_DN" --arg apw "$LDAP_ADMIN_PASSWORD" --arg bpw "$LDAP_RADIUS_PASSWORD" \
    '{generation:1, base_dn:$base, previous_base_dn:null,
      admin_rdn:"cn=admin", admin_password:$apw,
      bind_rdn:"cn=radius,ou=ServiceAccounts", bind_password:$bpw}' > "$SETTINGS.tmp"
  chmod 600 "$SETTINGS.tmp"
  mv "$SETTINGS.tmp" "$SETTINGS"
}

render() { # $1=出力先
  LDAP_BASE_DN=$(get .base_dn)
  LDAP_ADMIN_DN="$(get .admin_rdn),$LDAP_BASE_DN"
  LDAP_BIND_DN="$(get .bind_rdn),$LDAP_BASE_DN"
  LDAP_ADMIN_PASSWORD_HASH=$(slappasswd -s "$(get .admin_password)")
  export LDAP_BASE_DN LDAP_ADMIN_DN LDAP_BIND_DN LDAP_ADMIN_PASSWORD_HASH
  envsubst '${SCHEMA_DIR} ${MODULE_DIR} ${RUN_DIR} ${DATA_DIR} ${LDAP_BASE_DN} ${LDAP_ADMIN_DN} ${LDAP_ADMIN_PASSWORD_HASH} ${LDAP_BIND_DN}' \
    < "$TEMPLATES/slapd.conf.tmpl" > "$1.tmp"
  chown "root:$SLAPD_USER" "$1.tmp"
  chmod 640 "$1.tmp"
  mv "$1.tmp" "$1"
}

wait_ready() {
  i=0
  until ldapsearch -x -H "$LOCAL_URI" -b "" -s base >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -ge 30 ] && return 1
    kill -0 "$PID" 2>/dev/null || return 1
    sleep 1
  done
}

start_slapd() { # $1=URL
  slapd -d "${LDAP_LOG_LEVEL:-256}" -h "$1" -f "$CONF" -u "$SLAPD_USER" -g "$SLAPD_USER" &
  PID=$!
}

stop_slapd() {
  [ -n "$PID" ] || return 0
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  PID=""
}

bootstrap() {
  log "初期データを投入します"
  start_slapd "$LOCAL_URI/"
  wait_ready || { log "slapd が起動しませんでした"; exit 1; }
  LDAP_DOMAIN=$(echo "$LDAP_BASE_DN" | sed -e 's/[Dd][Cc]=//g' -e 's/,/./g')
  LDAP_DC=$(echo "$LDAP_DOMAIN" | cut -d. -f1)
  LDAP_BIND_PASSWORD=$(get .bind_password)
  export LDAP_DOMAIN LDAP_DC LDAP_BIND_PASSWORD
  if [ "$SLOT" = legacy ]; then
    envsubst '${LDAP_BASE_DN} ${LDAP_DOMAIN} ${LDAP_DC} ${LDAP_BIND_DN} ${LDAP_BIND_PASSWORD} ${LDAP_USER1_PASSWORD} ${LDAP_USER2_PASSWORD}' < "$TEMPLATES/bootstrap.ldif.tmpl"
  else
    printf 'dn: %s\nobjectClass: dcObject\nobjectClass: organization\ndc: %s\no: %s\n\n' "$LDAP_BASE_DN" "$LDAP_DC" "$LDAP_DOMAIN"
    for ou in Users Groups ServiceAccounts; do
      printf 'dn: ou=%s,%s\nobjectClass: organizationalUnit\nou: %s\n\n' "$ou" "$LDAP_BASE_DN" "$ou"
    done
    printf 'dn: %s\nobjectClass: organizationalRole\nobjectClass: simpleSecurityObject\ncn: radius\nuserPassword:: %s\n\n' "$LDAP_BIND_DN" "$(printf '%s' "$LDAP_BIND_PASSWORD" | base64 | tr -d '\n')"
  fi > "$RUN_DIR/seed.ldif"
  ldapadd -x -H "$LOCAL_URI" -D "$LDAP_ADMIN_DN" -w "$(get .admin_password)" -f "$RUN_DIR/seed.ldif"
  rm -f "$RUN_DIR/seed.ldif"
  stop_slapd
  touch "$MARKER"
  log "初期データの投入が完了しました"
}

trap 'stop_slapd; exit 0' TERM INT

seed_settings
if [ -f "$IMPORT" ]; then
  log "旧移行の import.ldif が残っています。確認・退避してから再起動してください"
  exit 1
fi

while :; do
  cp "$SETTINGS" "$RUN_DIR/settings.json"
  live_settings=$SETTINGS
  SETTINGS=$RUN_DIR/settings.json
  GEN=$(get .generation)
  CUR_BASE=$(get .base_dn)
  SLOT=$(jq -r '.data_slot // "legacy"' "$SETTINGS")
  case "$SLOT" in
    legacy) DATA_DIR=$DATA_ROOT ;;
    *) if ! printf '%s\n' "$SLOT" | grep -Eq '^[a-f0-9]{32}$'; then exit 1; fi
       DATA_DIR=$DATA_ROOT/domains/$SLOT ;;
  esac
  export DATA_DIR
  MARKER=$DATA_DIR/.bootstrapped
  mkdir -p "$DATA_DIR"
  chown "$SLAPD_USER:$SLAPD_USER" "$DATA_DIR"
  if [ "$SLOT" != legacy ]; then
    chown "$SLAPD_USER:$SLAPD_USER" "$DATA_ROOT" "$DATA_ROOT/domains"
  fi
  render "$CONF"
  if [ "$SLOT" = legacy ] && [ -f "$MARKER" ]; then
    chown "$SLAPD_USER:$SLAPD_USER" "$DATA_DIR/data.mdb"
    if [ -e "$DATA_DIR/lock.mdb" ]; then chown "$SLAPD_USER:$SLAPD_USER" "$DATA_DIR/lock.mdb"; fi
  fi
  if [ ! -f "$MARKER" ]; then
    if [ -e "$DATA_DIR/data.mdb" ]; then
      log "初期化情報のない既存MDBを保護するため停止します: $CUR_BASE"
      exit 1
    fi
    write_status "$GEN" false "LDAPの初期化中、または初期化で停止しました"
    bootstrap
  fi

  start_slapd "$SLAPD_URLS"
  if ! wait_ready; then
    write_status "$GEN" false "slapd の起動に失敗しました（docker compose logs openldap を確認してください）"
    log "slapd の起動に失敗しました"
    exit 1
  fi
  write_status "$GEN" true "反映済み"
  SETTINGS=$live_settings
  log "slapd 起動 (generation=$GEN, base=$CUR_BASE)"

  while :; do
    sleep 2
    if ! kill -0 "$PID" 2>/dev/null; then
      log "slapd が停止しました"
      exit 1
    fi
    NEW=$(get .generation 2>/dev/null || echo "$GEN")
    if [ "$NEW" != "$GEN" ]; then
      log "設定変更を検出 ($GEN -> $NEW)。slapd を再起動します"
      stop_slapd
      break
    fi
  done
done

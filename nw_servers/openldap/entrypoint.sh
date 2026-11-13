#!/bin/sh
# nw-openldap: 共有設定(settings.json)を監視し、変更があれば slapd を再起動する。
# ドメイン変更時は Web UI が書き出した import.ldif を取り込む（取り込み前にバックアップ）。
set -eu

SHARED=${SHARED_DIR:-/shared}
SETTINGS=$SHARED/settings.json
STATUS=$SHARED/status-ldap.json
IMPORT=$SHARED/import.ldif
BACKUP=$SHARED/backup
TEMPLATES=${TEMPLATE_DIR:-/templates}
CONF=${SLAPD_CONF:-/etc/openldap/slapd.conf}

export SCHEMA_DIR=${SCHEMA_DIR:-/etc/openldap/schema}
export MODULE_DIR=${MODULE_DIR:-/usr/lib/openldap}
export RUN_DIR=${RUN_DIR:-/run/openldap}
export DATA_DIR=${DATA_DIR:-/var/lib/openldap/openldap-data}
SLAPD_USER=${SLAPD_USER:-ldap}
SLAPD_URLS=${SLAPD_URLS:-ldap:///}
LOCAL_URI=${LOCAL_URI:-ldap://127.0.0.1}

MARKER=$DATA_DIR/.bootstrapped
PID=""
ERR=""

log() { echo "[openldap] $*"; }
now() { date '+%Y-%m-%dT%H:%M:%S%z'; }
get() { jq -r "$1" "$SETTINGS"; }

mkdir -p "$RUN_DIR" "$DATA_DIR" "$BACKUP"
chown -R "$SLAPD_USER:$SLAPD_USER" "$RUN_DIR" "$DATA_DIR"

write_status() { # $1=generation $2=true/false $3=message
  jq -n --argjson g "$1" --argjson ok "$2" --arg m "$3" --arg t "$(now)" --arg b "$(get .base_dn)" \
    '{applied_generation:$g, ok:$ok, message:$m, base_dn:$b, time:$t}' > "$STATUS.tmp"
  mv "$STATUS.tmp" "$STATUS"
}

seed_settings() {
  [ -s "$SETTINGS" ] && return 0
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
  envsubst '${LDAP_BASE_DN} ${LDAP_DOMAIN} ${LDAP_DC} ${LDAP_BIND_DN} ${LDAP_BIND_PASSWORD} ${LDAP_USER1_PASSWORD} ${LDAP_USER2_PASSWORD}' \
    < "$TEMPLATES/bootstrap.ldif.tmpl" \
    | ldapadd -x -H "$LOCAL_URI" -D "$LDAP_ADMIN_DN" -w "$(get .admin_password)"
  stop_slapd
  touch "$MARKER"
  log "初期データの投入が完了しました"
}

# ドメイン変更: import.ldif を検証 → 現DBをバックアップ → 入れ替え
apply_import() {
  ts=$(date +%Y%m%d-%H%M%S)
  render "$CONF.new"
  if ! slapadd -u -f "$CONF.new" -l "$IMPORT" > /tmp/slapadd.log 2>&1; then
    ERR="ドメイン変更に失敗したため元に戻しました: $(tail -3 /tmp/slapadd.log | tr '\n' ' ')"
    log "$ERR"
    rm -f "$CONF.new"
    mv "$IMPORT" "$BACKUP/import-failed-$ts.ldif"
    jq --arg b "$CUR_BASE" '.base_dn=$b | .previous_base_dn=null | .generation += 1' "$SETTINGS" > "$SETTINGS.tmp"
    chmod 600 "$SETTINGS.tmp"
    mv "$SETTINGS.tmp" "$SETTINGS"
    return 1
  fi
  if [ -f "$MARKER" ]; then
    slapcat -f "$CONF" -o ldif-wrap=no > "$BACKUP/before-domain-change-$ts.ldif"
    log "変更前のデータを backup/before-domain-change-$ts.ldif に保存しました"
  fi
  find "$DATA_DIR" -maxdepth 1 -name '*.mdb' -delete
  mv "$CONF.new" "$CONF"
  slapadd -f "$CONF" -l "$IMPORT"
  chown -R "$SLAPD_USER:$SLAPD_USER" "$DATA_DIR"
  mv "$IMPORT" "$BACKUP/imported-$ts.ldif"
  touch "$MARKER"
  log "ドメインを $(get .base_dn) に変更しました"
}

trap 'stop_slapd; exit 0' TERM INT

seed_settings
render "$CONF"
CUR_BASE=$(get .base_dn)
if [ ! -f "$MARKER" ] && [ ! -f "$IMPORT" ]; then
  bootstrap
fi

while :; do
  KEEP_ERR=0
  if [ -f "$IMPORT" ]; then
    apply_import || KEEP_ERR=1
  fi
  GEN=$(get .generation)
  render "$CONF"
  CUR_BASE=$(get .base_dn)
  if [ -f "$MARKER" ]; then
    slapindex -q -f "$CONF" 2>/dev/null || true
    chown -R "$SLAPD_USER:$SLAPD_USER" "$DATA_DIR"
  fi

  start_slapd "$SLAPD_URLS"
  if ! wait_ready; then
    write_status "$GEN" false "slapd の起動に失敗しました（docker compose logs openldap を確認してください）"
    log "slapd の起動に失敗しました"
    exit 1
  fi
  if [ "$KEEP_ERR" = 1 ]; then
    write_status "$GEN" false "$ERR"
  else
    write_status "$GEN" true "反映済み"
  fi
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

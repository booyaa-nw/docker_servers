#!/bin/bash
# nw-freeradius: 共有設定(settings.json)と Dynamic VLAN ポリシーを監視し、
# 変更があれば設定を検証(-C)してから FreeRADIUS を再起動する。
set -u

SHARED=${SHARED_DIR:-/shared}
SETTINGS=$SHARED/settings.json
POLICY_SRC=$SHARED/freeradius/nwlab_vlan
STATUS=$SHARED/status-radius.json
R=${RADDB:-/etc/freeradius/3.0}
TEMPLATES=${TEMPLATE_DIR:-/templates}
export LDAP_SERVER=${LDAP_SERVER:-nw-openldap}
PID=""

log() { echo "[freeradius] $*"; }
now() { date '+%Y-%m-%dT%H:%M:%S%z'; }

mkdir -p "$SHARED/freeradius" /tmp/nwlab-stage /tmp/nwlab-prev
until [ -s "$SETTINGS" ]; do log "settings.json を待っています"; sleep 2; done

default_policy() {
  cat <<'EOF'
# 自動生成前の既定ポリシー（ユーザー個別VLANのみ評価）
nwlab_vlan {
	if (&control:Tmp-String-0 && (&control:Tmp-String-0 =~ /^[0-9]+$/)) {
		update reply {
			&Tunnel-Type := VLAN
			&Tunnel-Medium-Type := IEEE-802
			&Tunnel-Private-Group-Id := "%{control:Tmp-String-0}"
		}
	}
}
EOF
}

signature() {
  local g p
  g=$(jq -r .generation "$SETTINGS" 2>/dev/null || echo 0)
  if [ -f "$POLICY_SRC" ]; then p=$(md5sum < "$POLICY_SRC" | cut -d' ' -f1); else p=none; fi
  echo "$g-$p"
}

write_status() { # $1=signature $2=true/false $3=message
  jq -n --arg s "$1" --argjson ok "$2" --arg m "$3" --arg t "$(now)" \
    '{applied_signature:$s, ok:$ok, message:$m, time:$t}' > "$STATUS.tmp" && mv "$STATUS.tmp" "$STATUS"
}

# 新しい設定を配置して -C で検証。失敗したら元に戻す
apply() {
  export LDAP_BASE_DN LDAP_BIND_DN LDAP_BIND_PW
  LDAP_BASE_DN=$(jq -r .base_dn "$SETTINGS")
  LDAP_BIND_DN="$(jq -r .bind_rdn "$SETTINGS"),$LDAP_BASE_DN"
  LDAP_BIND_PW=$(jq -r .bind_password "$SETTINGS")
  envsubst '${LDAP_SERVER} ${LDAP_BASE_DN} ${LDAP_BIND_DN}' < "$TEMPLATES/ldap.tmpl" > /tmp/nwlab-stage/ldap
  if [ -s "$POLICY_SRC" ]; then cp "$POLICY_SRC" /tmp/nwlab-stage/nwlab_vlan; else default_policy > /tmp/nwlab-stage/nwlab_vlan; fi

  rm -f /tmp/nwlab-prev/*
  [ -f "$R/mods-enabled/ldap" ] && cp -p "$R/mods-enabled/ldap" /tmp/nwlab-prev/ldap
  [ -f "$R/policy.d/nwlab_vlan" ] && cp -p "$R/policy.d/nwlab_vlan" /tmp/nwlab-prev/nwlab_vlan
  install -m 640 -o root -g freerad /tmp/nwlab-stage/ldap "$R/mods-enabled/ldap"
  install -m 640 -o root -g freerad /tmp/nwlab-stage/nwlab_vlan "$R/policy.d/nwlab_vlan"

  if freeradius -C -l stdout > /tmp/nwlab-check.log 2>&1; then
    return 0
  fi
  log "設定の検証に失敗しました:"
  tail -20 /tmp/nwlab-check.log
  [ -f /tmp/nwlab-prev/ldap ] && cp -p /tmp/nwlab-prev/ldap "$R/mods-enabled/ldap"
  [ -f /tmp/nwlab-prev/nwlab_vlan ] && cp -p /tmp/nwlab-prev/nwlab_vlan "$R/policy.d/nwlab_vlan"
  return 1
}

start() {
  if [ "${RADIUS_DEBUG:-0}" = "1" ]; then
    freeradius -X &
  else
    freeradius -f -l stdout &
  fi
  PID=$!
  sleep 2
  if ! kill -0 "$PID" 2>/dev/null; then
    write_status "$SIG" false "FreeRADIUS が起動直後に停止しました（docker compose logs freeradius を確認してください）"
    log "freeradius が起動直後に停止しました"
    exit 1
  fi
}

stop() {
  [ -n "$PID" ] || return 0
  kill "$PID" 2>/dev/null
  wait "$PID" 2>/dev/null
  PID=""
}

trap 'stop; exit 0' TERM INT

SIG=$(signature)
if ! apply; then
  write_status "$SIG" false "FreeRADIUS の設定検証に失敗しました: $(grep -iE 'error|fail' /tmp/nwlab-check.log | tail -2 | tr '\n' ' ')"
  # 前回の正常な設定がなければ起動できない
  [ -f /tmp/nwlab-prev/ldap ] || exit 1
fi
start
write_status "$SIG" true "反映済み"
log "起動しました ($SIG)"

while sleep 2; do
  if ! kill -0 "$PID" 2>/dev/null; then
    log "freeradius が停止しました"
    exit 1
  fi
  NEW=$(signature)
  [ "$NEW" = "$SIG" ] && continue
  SIG=$NEW
  if apply; then
    log "設定変更を検出。再起動します ($SIG)"
    stop
    start
    write_status "$SIG" true "反映済み"
  else
    write_status "$SIG" false "FreeRADIUS の設定検証に失敗したため、前の設定で動作中です: $(grep -iE 'error|fail' /tmp/nwlab-check.log | tail -2 | tr '\n' ' ')"
  fi
done

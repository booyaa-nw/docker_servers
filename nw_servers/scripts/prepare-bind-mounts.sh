#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

command -v docker >/dev/null || { echo "docker CLI is required" >&2; exit 1; }
security_options=$(docker info --format '{{json .SecurityOptions}}')
host_uid=$(id -u)
host_gid=$(id -g)
if [[ "$security_options" == *rootless* ]]; then
  bind_uid=0
  bind_gid=0
  owner_uid=0
  owner_gid=0
  mode=rootless
  port_start=$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start 2>/dev/null || echo 1024)
  if (( port_start > 80 )) && [[ "${ROOTLESS_PRIVILEGED_PORTS:-0}" != 1 ]]; then
    cat >&2 <<EOF
Rootless Docker is active, but this stack publishes privileged host ports (80, 21, 25, and 69).
The current net.ipv4.ip_unprivileged_port_start is $port_start.
Configure the host to allow privileged ports for RootlessKit (for example, set
net.ipv4.ip_unprivileged_port_start=0), then restart Docker and rerun this script.
If RootlessKit already has CAP_NET_BIND_SERVICE, rerun with
ROOTLESS_PRIVILEGED_PORTS=1 in the environment.
EOF
    exit 1
  fi
else
  bind_uid=$host_uid
  bind_gid=$host_gid
  owner_uid=$host_uid
  owner_gid=$host_gid
  mode=rootful
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env from .env.example; set the required passwords and NETRIG_SECRET, then run setup.sh again."
fi

set_env() {
  local key=$1 value=$2 tmp
  tmp=$(mktemp .env.runtime.XXXXXX)
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    index($0, key "=") == 1 {
      if (!found) print key "=" value
      found=1
      next
    }
    { print }
    END { if (!found) print key "=" value }
  ' .env > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" .env
}
set_env BIND_UID "$bind_uid"
set_env BIND_GID "$bind_gid"
chmod 600 .env

directories=(
  openldap/config openldap/data shared/ldap
  freeradius/config ldapmgttooz/config ldapmgttooz/data
  netrig/config netrig/data
  mailpit/data mailpit/config mailpit/secrets
  plugins/log_view/data plugins/log_view/config/mibs
  caddy/config caddy/data
  public
  pki/data pki/import pki/offline pki/secrets ldap/certs
)
for path in "${directories[@]}"; do
  mkdir -p -- "$path"
done
mkdir -p shared/ldap/freeradius caddy/data/config

if [[ ! -e pki/secrets/root.pass ]]; then
  umask 077
  openssl rand -base64 48 > pki/secrets/root.pass
fi

files=(
  openldap/config/slapd.conf.tmpl
  openldap/config/bootstrap.ldif.tmpl
  freeradius/config/clients.conf
  freeradius/config/ldap.tmpl
  caddy/config/Caddyfile
  pki/secrets/root.pass
)
for path in "${files[@]}"; do
  [[ -f "$path" ]] || { echo "Required bind-mounted file is missing: $path" >&2; exit 1; }
done

mounts=()
targets=()
for path in "${directories[@]}"; do
  mounts+=(--volume "$ROOT/$path:/workspace/$path")
  targets+=("/workspace/$path")
done
docker run --rm --user 0:0 "${mounts[@]}" alpine:3.22 \
  chown -R "$owner_uid:$owner_gid" "${targets[@]}" >/dev/null

# The public share contains intentionally downloadable files.
chmod -R a+rX public

for path in "${directories[@]}" "${files[@]}"; do
  actual=$(stat -c '%u:%g' -- "$path")
  if [[ "$actual" != "$host_uid:$host_gid" ]]; then
    echo "Ownership check failed for $path: expected $host_uid:$host_gid, got $actual" >&2
    exit 1
  fi
done

echo "Prepared bind mounts for $mode Docker; host files and directories belong to $host_uid:$host_gid (container UID/GID $bind_uid:$bind_gid)."

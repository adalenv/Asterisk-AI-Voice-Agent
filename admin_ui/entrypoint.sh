#!/bin/sh
set -eu

if [ "$(id -u)" != "0" ]; then
  exec "$@"
fi

if [ -S /var/run/docker.sock ]; then
  sock_gid="$(stat -c '%g' /var/run/docker.sock 2>/dev/null || stat -f '%g' /var/run/docker.sock 2>/dev/null || echo "")"
  if [ -n "${sock_gid:-}" ] && echo "$sock_gid" | grep -Eq '^[0-9]+$'; then
    if ! id -G appuser 2>/dev/null | tr ' ' '\n' | grep -qx "$sock_gid"; then
      existing_group="$(awk -F: -v gid="$sock_gid" '$3==gid{print $1; exit}' /etc/group 2>/dev/null || true)"
      group_name="${existing_group:-dockersock}"
      if [ -z "${existing_group:-}" ]; then
        groupadd -g "$sock_gid" "$group_name" 2>/dev/null || true
      fi
      usermod -aG "$group_name" appuser 2>/dev/null || true
    fi
  fi
fi

exec gosu appuser "$@"

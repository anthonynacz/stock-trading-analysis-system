#!/usr/bin/env bash
# Set the username and password of the password gate (gate/Caddyfile), then restart it.
# Run on the server, in the project folder:  scripts/gate-user.sh <username>
# The password is asked for, never passed on the command line, and only its bcrypt hash is stored.
set -euo pipefail

user="${1:-}"
if ! [[ "$user" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "usage: scripts/gate-user.sh <username>   (letters, digits, . _ -)" >&2
  exit 2
fi
cd "$(dirname "$(readlink -f "$0")")/.."

read -rs -p "Password: " pw; echo
read -rs -p "Again:    " pw2; echo
if [ -z "$pw" ] || [ "$pw" != "$pw2" ]; then
  echo "gate: the passwords are empty or differ; nothing changed" >&2
  exit 1
fi
hash=$(printf '%s' "$pw" | docker run --rm -i caddy:2 caddy hash-password)
unset pw pw2
case "$hash" in
  '$2'*) ;;
  *) echo "gate: could not hash the password; nothing changed" >&2; exit 1 ;;
esac

umask 077
printf '%s %s\n' "$user" "$hash" > gate/users.caddy
echo "gate: saved the login for '$user'"
if grep -Eq '^COMPOSE_PROFILES=.*gate' .env 2>/dev/null; then
  docker compose up -d --force-recreate gate
else
  echo "gate: not enabled here (COMPOSE_PROFILES=gate is not in .env), so nothing was restarted"
fi

#!/usr/bin/env bash
# Server-side deploy: fast-forward this checkout to origin/main and rebuild the services whose code changed.
#
# Run by the GitHub Actions deploy key through an SSH forced command (docs/deploy.md), where the request
# arrives in SSH_ORIGINAL_COMMAND, or by hand on the server:  scripts/deploy.sh [auto|all|backend|radar|frontend|status]
#
#   auto      rebuild what the pulled commits touched (backend/ -> backend + radar, frontend/ -> frontend,
#             docker-compose.yml -> everything; *.md files never count); nothing to pull -> nothing to do
#   all       rebuild backend, radar and frontend whatever changed
#   backend   backend + radar (they share one image)      radar / frontend   that service only
#   status    print the deployed commit and the containers; changes nothing
#
# The request is checked against that list and nothing else from the caller is ever executed.
set -euo pipefail

main() {
  local want="${SSH_ORIGINAL_COMMAND:-${1:-auto}}"
  case "$want" in
    auto | all | backend | radar | frontend | status) ;;
    *)
      echo "deploy: unknown request (use auto, all, backend, radar, frontend or status)" >&2
      exit 2
      ;;
  esac

  cd "$(dirname "$(readlink -f "$0")")/.."

  if [ "$want" = status ]; then
    git log --oneline -1
    docker compose ps
    exit 0
  fi

  # One deploy at a time: a second one waits for the first (up to 10 minutes).
  exec 9> /tmp/edgeflow-deploy.lock
  if ! flock -w 600 9; then
    echo "deploy: another deploy is still running" >&2
    exit 1
  fi

  local old new changed
  old=$(git rev-parse HEAD)
  # An untracked file in the way (a stray stub left by a container mount) fails here, loudly: remove it and re-run.
  git pull -q --ff-only
  new=$(git rev-parse HEAD)
  # Notes and docs (*.md) rebuild nothing, wherever they live.
  changed=$(git diff --name-only "$old" "$new" | grep -v '\.md$' || true)

  local -a services=()
  case "$want" in
    all) services=(backend radar frontend) ;;
    backend) services=(backend radar) ;;
    radar) services=(radar) ;;
    frontend) services=(frontend) ;;
    auto)
      if grep -q '^docker-compose\.yml$' <<< "$changed"; then
        services=(backend radar frontend)
      else
        if grep -q '^backend/' <<< "$changed"; then services+=(backend radar); fi
        if grep -q '^frontend/' <<< "$changed"; then services+=(frontend); fi
      fi
      ;;
  esac

  echo "deploy: $(git log --oneline -1)"
  if [ "${#services[@]}" -eq 0 ]; then
    echo "deploy: no service code changed (${old:0:7} -> ${new:0:7}); nothing rebuilt"
    exit 0
  fi

  echo "deploy: rebuilding ${services[*]}"
  docker compose up -d --build "${services[@]}"

  # The backend applies migrations before it listens: give it time. Any HTTP answer below 500 means it is up.
  local code=000 i
  for i in $(seq 1 45); do
    code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://localhost:8000/api/radar/health || true)
    if [ "$code" != 000 ] && [ "$code" -lt 500 ]; then break; fi
    sleep 4
  done
  if [ "$code" = 000 ] || [ "$code" -ge 500 ]; then
    echo "deploy: the backend does not answer (HTTP $code) after the rebuild" >&2
    docker compose ps
    exit 1
  fi
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' http://localhost:3000/ || true)
  if [ "$code" != 200 ]; then
    echo "deploy: the frontend does not answer (HTTP $code) after the rebuild" >&2
    docker compose ps
    exit 1
  fi
  docker compose ps --format '{{.Name}} {{.Status}}'
  echo "deploy: done, ${new:0:7} is live"
}

main "$@"

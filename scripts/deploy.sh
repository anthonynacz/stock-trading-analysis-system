#!/usr/bin/env bash
# Server-side deploy: fast-forward this checkout to origin/main and rebuild the services whose code changed.
#
# Run by the GitHub Actions deploy key through an SSH forced command (docs/deploy.md), where the request
# arrives in SSH_ORIGINAL_COMMAND, or by hand on the server:  scripts/deploy.sh [auto|all|backend|radar|frontend|gate|status|quant-backfill|quant-audit]
#
#   auto      rebuild what the pulled commits touched (backend/ -> backend + radar, frontend/ -> frontend,
#             docker-compose.yml -> everything; *.md files never count); nothing to pull -> nothing to do
#   all       rebuild backend, radar and frontend whatever changed
#   backend   backend + radar (they share one image)      radar / frontend   that service only
#   gate      restart the password gate only (gate/Caddyfile, docs/deploy.md); it also restarts with all,
#             and with auto when gate/ or docker-compose.yml changed. Skipped unless .env enables it.
#   status    print the deployed commit and the containers; changes nothing
#   quant-backfill   no pull, no rebuild: in the running backend container, fill quant_daily_bars (a 10-year
#             backfill for tickers without bars), score the model, write the forecasts (backend/quant/CLAUDE.md)
#   quant-audit   changes nothing: in the running backend container, audit quant_daily_bars and print the
#             model's scorecard with its per-ticker breakdown (evaluation with --no-record; nothing written)
#
# The request is checked against that list and nothing else from the caller is ever executed.
set -euo pipefail

main() {
  local want="${SSH_ORIGINAL_COMMAND:-${1:-auto}}"
  case "$want" in
    auto | all | backend | radar | frontend | gate | status | quant-backfill | quant-audit) ;;
    *)
      echo "deploy: unknown request (use auto, all, backend, radar, frontend, gate, status, quant-backfill or quant-audit)" >&2
      exit 2
      ;;
  esac

  cd "$(dirname "$(readlink -f "$0")")/.."

  if [ "$want" = quant-audit ]; then
    # Read-only: no pull, no rebuild, no lock needed, nothing recorded. Runs the code deployed in the container.
    echo "quant: $(git log --oneline -1)"
    docker compose exec -T backend python -m quant.audit || echo "quant: the audit failed" >&2
    docker compose exec -T backend python -m quant.evaluate --no-record || echo "quant: the evaluation failed" >&2
    exit 0
  fi

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

  if [ "$want" = quant-backfill ]; then
    echo "quant: $(git log --oneline -1)"
    # Bars must succeed; a failed evaluation is reported and the forecast still runs on the stored bars.
    docker compose exec -T backend python -m quant.run bars
    docker compose exec -T backend python -m quant.evaluate || echo "quant: the evaluation failed" >&2
    docker compose exec -T backend python -m quant.run forecast
    echo "quant: backfill done"
    exit 0
  fi

  local old new changed
  old=$(git rev-parse HEAD)
  # An untracked file in the way (a stray stub left by a container mount) fails here, loudly: remove it and re-run.
  git pull -q --ff-only
  new=$(git rev-parse HEAD)
  # Notes and docs (*.md) rebuild nothing, wherever they live.
  changed=$(git diff --name-only "$old" "$new" | grep -v '\.md$' || true)

  # The password gate runs only where .env enables its compose profile (the server).
  local gate=no
  if grep -Eq '^COMPOSE_PROFILES=.*gate' .env 2> /dev/null; then
    case "$want" in
      all | gate) gate=yes ;;
      auto) if grep -Eq '^(gate/|docker-compose\.yml$)' <<< "$changed"; then gate=yes; fi ;;
    esac
  elif [ "$want" = gate ]; then
    echo "deploy: the gate is not enabled here (COMPOSE_PROFILES=gate is not in .env)" >&2
    exit 1
  fi

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
  if [ "${#services[@]}" -eq 0 ] && [ "$gate" = no ]; then
    echo "deploy: no service code changed (${old:0:7} -> ${new:0:7}); nothing rebuilt"
    exit 0
  fi

  if [ "${#services[@]}" -gt 0 ]; then
    echo "deploy: rebuilding ${services[*]}"
    docker compose up -d --build "${services[@]}"
  fi
  if [ "$gate" = yes ]; then
    echo "deploy: restarting the password gate"
    docker compose up -d --force-recreate gate
  fi

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
  if [ "$gate" = yes ]; then
    # The gate must answer on the site's own name, over HTTPS, and ask for the password (401).
    local site
    site=$(grep -m1 '^SITE_ADDRESS=' .env | cut -d= -f2- || true)
    code=000
    for i in $(seq 1 15); do
      code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' --resolve "$site:443:127.0.0.1" "https://$site/" || true)
      if [ "$code" = 401 ]; then break; fi
      sleep 4
    done
    if [ "$code" != 401 ]; then
      echo "deploy: the password gate does not ask for a password on https://$site/ (HTTP $code)" >&2
      docker compose logs --tail=20 gate >&2 || true
      exit 1
    fi
    echo "deploy: the password gate is up on https://$site/"
  fi
  docker compose ps --format '{{.Name}} {{.Status}}'
  echo "deploy: done, ${new:0:7} is live"
}

main "$@"

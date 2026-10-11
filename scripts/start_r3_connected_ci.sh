#!/usr/bin/env bash
set -euo pipefail
: "${RUNNER_TEMP:?Only an owned ephemeral runner}"
: "${GITHUB_RUN_ID:?Only an owned CI run}"
: "${OWNER_PREVIEW_FEATURE_SHA:?Exact checkout SHA required}"
test "$(git rev-parse HEAD)" = "$OWNER_PREVIEW_FEATURE_SHA"
export APP_ENV=test
export R3_PILOT_CONTAINER="r3-connected-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT:-1}"
# No network-exposed DB port: the fresh container uses host networking with a
# server that listens only on 127.0.0.1. The official entrypoint requires its
# container-private Unix socket while initializing with TCP disabled. That socket
# is never host-mounted. HBA trust is restricted to this ephemeral runner; the
# bootstrap role is made NOLOGIN before the application starts.
docker run --detach --name "$R3_PILOT_CONTAINER" --network host \
  -e POSTGRES_USER=r3_pilot_bootstrap -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_DB=greenhouse_owner_os_test postgres:17.11 \
  -c listen_addresses=127.0.0.1 > "$RUNNER_TEMP/r3-container.id"
echo "R3_PILOT_CONTAINER=$R3_PILOT_CONTAINER" >> "$GITHUB_ENV"
# Preserve the owned container startup log on success and failure; cleanup is
# performed only after artifact upload by the workflow always-step.
trap 'docker logs "$R3_PILOT_CONTAINER" > artifacts/r3-connected/postgres-startup.log 2>&1 || true' EXIT
ready=0
for i in $(seq 1 60); do
  if docker exec "$R3_PILOT_CONTAINER" pg_isready -h 127.0.0.1 -U r3_pilot_bootstrap -d greenhouse_owner_os_test > /dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
test "$ready" = 1 || { docker logs "$R3_PILOT_CONTAINER"; exit 1; }
export R3_BOOTSTRAP_DATABASE_URL='postgresql+psycopg://r3_pilot_bootstrap@127.0.0.1:5432/greenhouse_owner_os_test'
DATABASE_URL="$R3_BOOTSTRAP_DATABASE_URL" alembic upgrade head
python scripts/r3_connected_runtime.py
export DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:5432/greenhouse_owner_os_test'
export TEST_DATABASE_URL="$DATABASE_URL" R3_CONNECTED_PILOT=1 PREVIEW_BIND=127.0.0.1 PREVIEW_PORT=4173
: "${PREVIEW_SECRET:=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')}"
echo "::add-mask::$PREVIEW_SECRET"
export PREVIEW_SECRET
python -m greenhouse_owner_os.preview > "$RUNNER_TEMP/r3-connected-server.log" 2>&1 &
echo $! > "$RUNNER_TEMP/r3-connected-server.pid"
for i in $(seq 1 60); do
  if curl --silent --fail http://127.0.0.1:4173/health > /dev/null; then break; fi
  if ! kill -0 "$(cat "$RUNNER_TEMP/r3-connected-server.pid")" 2>/dev/null; then cat "$RUNNER_TEMP/r3-connected-server.log"; exit 1; fi
  sleep 1
done
curl --silent --fail http://127.0.0.1:4173/health > /dev/null
{
 echo "R3_PILOT_CONTAINER=$R3_PILOT_CONTAINER"
 echo "PREVIEW_SECRET=$PREVIEW_SECRET"
 echo "APP_ENV=test"
 echo "R3_CONNECTED_PILOT=1"
 echo "DATABASE_URL=$DATABASE_URL"
 echo "TEST_DATABASE_URL=$TEST_DATABASE_URL"
} >> "$GITHUB_ENV"

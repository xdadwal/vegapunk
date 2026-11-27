#!/bin/sh
# Smoke test: build the image, start it under a throwaway Compose project on a free port,
# wait until Docker reports it healthy, check two endpoints, and always clean up.
#
#   sh scripts/smoke.sh
#
# It uses scripts/smoke.override.yaml, so it never touches a Vegapunk that is already
# running on this machine (the container named `vegapunk`, port 8000, its data volume).
#
# On a GitHub runner the Docker socket is root:docker with mode 660 and DOCKER_GID is not
# set, so the container cannot read Docker there and the pages would say so. That is fine
# and expected: health does not depend on Docker. Do not "fix" it here.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

PROJECT="vpsmoke$$"
WAIT=120 # seconds; the health check runs every 30 s, so the first result takes about 30 s

compose() {
    docker compose -p "$PROJECT" -f compose.yaml -f scripts/smoke.override.yaml "$@"
}

# A free high port: one nothing answers on (curl exits 7 when the connection is refused).
port=$((20000 + $$ % 20000))
tries=0
while :; do
    rc=0
    curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$port/" || rc=$?
    [ "$rc" -eq 7 ] && break
    tries=$((tries + 1))
    if [ "$tries" -ge 50 ]; then
        echo "smoke: no free port found" >&2
        exit 1
    fi
    port=$((port + 1))
done
VEGAPUNK_PORT=$port
export VEGAPUNK_PORT

ok=0
cleanup() {
    if [ "$ok" -ne 1 ]; then
        echo "smoke: FAILED; container logs follow" >&2
        compose ps -a >&2 || true
        compose logs --no-color >&2 || true
    fi
    # Removes this project's container, network, volume and the image it built.
    compose down -v --rmi local >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 1' INT TERM

echo "smoke: project $PROJECT, port $VEGAPUNK_PORT"
compose up -d --build

id=$(compose ps -q vegapunk)
elapsed=0
status=""
while [ "$elapsed" -lt "$WAIT" ]; do
    status=$(docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null || echo missing)
    [ "$status" = healthy ] && break
    if [ "$status" = unhealthy ]; then
        break
    fi
    sleep 3
    elapsed=$((elapsed + 3))
done
if [ "$status" != healthy ]; then
    echo "smoke: container is not healthy after ${elapsed}s (status: $status)" >&2
    exit 1
fi
echo "smoke: healthy after about ${elapsed}s"

base="http://127.0.0.1:$VEGAPUNK_PORT"
for path in /api/health /api/setup/status; do
    printf 'smoke: GET %s -> ' "$path"
    curl -fsS --max-time 10 "$base$path"
    echo
done

ok=1
echo "smoke: passed"

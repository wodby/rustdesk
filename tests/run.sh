#!/usr/bin/env bash

set -e

if [[ -n "${DEBUG}" ]]; then
    set -x
fi

volume="${NAME}-data"
cid=""

cleanup() {
    if [[ -n "${cid}" ]]; then
        docker rm -vf "${cid}" > /dev/null
    fi
    docker volume rm -f "${volume}" > /dev/null
}
trap cleanup EXIT

start() {
    cid="$(docker run -d -e RUSTDESK_HOST=rustdesk.example.com -e RUSTDESK_ID_PORT=31999 -e RUSTDESK_RELAY_PORT=31998 -v "${volume}:/data" --name "${NAME}" "${IMAGE}")"
}

run() {
    docker exec "${cid}" "${@}"
}

wait_ready() {
    for _ in $(seq 1 30); do
        if run curl -fsS -o /dev/null http://localhost:8080/healthz 2>/dev/null; then
            return
        fi
        sleep 1
    done
    docker logs "${cid}"
    return 1
}

# Prints the status line of a WebSocket handshake. The upgraded connection stays open, so curl times out.
handshake() {
    run curl -sS -i --http1.1 --max-time 2 \
        -H 'Connection: Upgrade' \
        -H 'Upgrade: websocket' \
        -H 'Sec-WebSocket-Version: 13' \
        -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
        "http://localhost:8080${1}" 2>/dev/null | head -n 1 || true
}

start

echo -n "Waiting for the server... "
wait_ready
echo "OK"

echo -n "Checking the server version... "
run hbbs --version | grep -q "${RUSTDESK_VER}"
run hbbr --version | grep -q "${RUSTDESK_VER}"
echo "OK"

echo -n "Checking the server runs as a regular user... "
[[ "$(run id -u)" == "1000" ]]
echo "OK"

echo -n "Checking the ID server, relay and router run... "
for process in hbbs hbbr; do
    run pgrep -x "${process}" > /dev/null
done
run ps -o args | grep -q '^hbbr -k _$'
run ps -o args | grep -q '^hbbs -r rustdesk.example.com:31998$'
run ps -o args | grep -q "^nginx: master process"
echo "OK"

echo -n "Checking the key pair is on the volume... "
key="$(run cat /data/id_ed25519.pub)"
[[ -n "${key}" ]]
run test -s /data/id_ed25519
echo "OK"

echo -n "Checking WebSocket routes... "
handshake /ws/id | grep -q ' 101 '
handshake /ws/relay | grep -q ' 101 '
echo "OK"

# The relay takes loopback connections for its admin console, so devices use the container's address.
echo "Checking a relayed session between two devices..."
address="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${cid}")"
docker run --rm -i --network "container:${cid}" -v "${PWD}/probe.py:/probe.py:ro" python:3-alpine \
    python -I /probe.py "${address}" 21116 21117 "${key}"

echo -n "Checking other paths... "
run curl -fsS http://localhost:8080/ | grep -q 'RustDesk server'
[[ "$(run curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8080/api/heartbeat)" == "404" ]]
echo "OK"

echo -n "Checking client settings... "
settings="$(run rustdesk-client-settings)"
grep -q "ID server:    rustdesk.example.com:31999" <<< "${settings}"
grep -q "Relay server: rustdesk.example.com:31998" <<< "${settings}"
grep -q "Key:          ${key}" <<< "${settings}"
echo "OK"

echo -n "Checking the key survives a restart... "
docker rm -f "${cid}" > /dev/null
cid=""
start
wait_ready
[[ "$(run cat /data/id_ed25519.pub)" == "${key}" ]]
echo "OK"

echo -n "Checking the container stops when a server process exits... "
run pkill -x hbbr
for _ in $(seq 1 10); do
    if [[ "$(docker inspect -f '{{.State.Running}}' "${cid}")" == "false" ]]; then
        break
    fi
    sleep 1
done
[[ "$(docker inspect -f '{{.State.Running}}' "${cid}")" == "false" ]]
echo "OK"

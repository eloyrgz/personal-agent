#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$SCRIPT_DIR/.env"
    set +a
fi

services=(training-coach-api.service personal-agent-router.service training-coach-bridge.service training-coach-telegram.service personal-agent-mcp-bridge.service)

wait_for_health() {
    local name=$1 url=$2
    for ((attempt = 0; attempt < 60; attempt++)); do
        if curl --silent --show-error --fail --max-time 2 --output /dev/null "$url" 2>/dev/null; then
            printf '%s: ready\n' "$name"
            return 0
        fi
        sleep 2
    done
    printf '%s: did not become ready (%s)\n' "$name" "$url" >&2
    return 1
}

check_service() {
    local service=$1
    if systemctl --user is-active --quiet "$service"; then
        printf 'OK     service %-38s active\n' "$service"
    else
        printf 'FAIL   service %-38s inactive (inspect: journalctl --user -u %s -n 80)\n' "$service" "$service"
        result=1
    fi
}

check_http() {
    local name=$1 url=$2 response
    if response=$(curl --silent --show-error --fail --max-time 5 --output /dev/null --write-out '%{http_code}' "$url" 2>&1); then
        printf 'OK     endpoint %-36s HTTP %s\n' "$name" "$response"
    else
        printf 'FAIL   endpoint %-36s %s (%s)\n' "$name" "$response" "$url"
        result=1
    fi
}

check_mcp_tools() {
    local python="$SCRIPT_DIR/.venv/bin/python"
    if [[ ! -x "$python" ]]; then
        printf 'FAIL   MCP protocol check: missing Python environment at %s\n' "$python"
        result=1
        return
    fi

    if "$python" - <<'PY'
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    import os

    headers = {}
    token = os.getenv("MCP_BRIDGE_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with streamablehttp_client(
        "http://127.0.0.1:8102/mcp", headers=headers
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            if "ask_personal_agent" not in names:
                raise RuntimeError("ask_personal_agent is missing from the MCP tool list")
            print("OK     MCP protocol: initialized; ask_personal_agent is available")

asyncio.run(main())
PY
    then
        :
    else
        printf 'FAIL   MCP protocol: could not initialize/list tools (see error above)\n'
        result=1
    fi
}

check_stack() {
    result=0
    printf 'Checking Personal Agent stack...\n'
    for service in "${services[@]}"; do
        check_service "$service"
    done

    check_http 'Training Coach API' http://127.0.0.1:8000/health
    check_http 'Personal Agent router' http://127.0.0.1:8101/health
    check_http 'Personal Agent model endpoint' http://127.0.0.1:8101/v1/models
    check_http 'Training Coach bridge' http://127.0.0.1:8100/v1/models
    check_http 'Personal Agent MCP bridge' http://127.0.0.1:8102/health
    check_mcp_tools

    if (( result == 0 )); then
        printf 'All stack checks passed.\n'
    else
        printf 'One or more checks failed. Use the service/endpoint details above to locate the problem.\n' >&2
    fi
    return "$result"
}

case "${1:-}" in
    start|restart)
        systemctl --user "$1" training-coach-api.service
        wait_for_health 'Training Coach API' http://127.0.0.1:8000/health
        systemctl --user "$1" personal-agent-router.service training-coach-bridge.service training-coach-telegram.service
        wait_for_health 'Personal Agent router' http://127.0.0.1:8101/health
        systemctl --user "$1" personal-agent-mcp-bridge.service
        wait_for_health 'Personal Agent MCP bridge' http://127.0.0.1:8102/health
        wait_for_health 'Training Coach bridge' http://127.0.0.1:8100/v1/models
        systemctl --user is-active --quiet "${services[@]}"
        printf 'Stack is running.\n'
        ;;
    stop)
        systemctl --user stop personal-agent-mcp-bridge.service personal-agent-router.service training-coach-bridge.service training-coach-telegram.service training-coach-api.service
        ;;
    status)
        result=0
        for service in "${services[@]}"; do
            if systemctl --user is-active --quiet "$service"; then
                printf '%s: active\n' "$service"
            else
                printf '%s: inactive\n' "$service"
                result=1
            fi
        done
        exit "$result"
        ;;
    check)
        check_stack
        ;;
    *)
        printf 'Usage: %s {start|restart|stop|status|check}\n' "$0" >&2
        exit 2
        ;;
esac

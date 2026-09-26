#!/usr/bin/env bash
set -euo pipefail

services=(training-coach-api.service personal-agent-router.service training-coach-bridge.service training-coach-telegram.service)

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

case "${1:-}" in
    start|restart)
        systemctl --user "$1" training-coach-api.service
        wait_for_health 'Training Coach API' http://127.0.0.1:8000/health
        systemctl --user "$1" personal-agent-router.service training-coach-bridge.service training-coach-telegram.service
        wait_for_health 'Personal Agent router' http://127.0.0.1:8101/health
        wait_for_health 'Training Coach bridge' http://127.0.0.1:8100/v1/models
        systemctl --user is-active --quiet "${services[@]}"
        printf 'Stack is running.\n'
        ;;
    stop)
        systemctl --user stop personal-agent-router.service training-coach-bridge.service training-coach-telegram.service training-coach-api.service
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
    *)
        printf 'Usage: %s {start|restart|stop|status}\n' "$0" >&2
        exit 2
        ;;
esac
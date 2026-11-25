#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd -P)"

cmd="${1:-ensure}"
if [ $# -gt 0 ]; then
  shift
fi

case "$cmd" in
  open|ensure)
    exec "${REPO_ROOT}/scripts/ensure-server.sh" --ensure "$@"
    ;;
  status)
    exec "${REPO_ROOT}/scripts/ensure-server.sh" --status "$@"
    ;;
  stop)
    exec "${REPO_ROOT}/scripts/ensure-server.sh" --stop "$@"
    ;;
  rates)
    exec python3 "${REPO_ROOT}/scripts/update_rates.py" "$@"
    ;;
  verify)
    exec python3 "${REPO_ROOT}/scripts/install.py" --verify "$@"
    ;;
  *)
    echo "Usage: board.sh [ensure|open|status|stop|rates|verify] [args...]" >&2
    exit 2
    ;;
esac

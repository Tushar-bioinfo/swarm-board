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
    exec "${REPO_ROOT}/scripts/ensure-server.sh" --ensure ${1+"$@"}
    ;;
  status)
    exec "${REPO_ROOT}/scripts/ensure-server.sh" --status ${1+"$@"}
    ;;
  stop)
    exec "${REPO_ROOT}/scripts/ensure-server.sh" --stop ${1+"$@"}
    ;;
  rates)
    exec python3 "${REPO_ROOT}/scripts/update_rates.py" ${1+"$@"}
    ;;
  labels)
    PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" exec python3 -m swarm_board.labeler ${1+"$@"}
    ;;
  verify)
    exec python3 "${REPO_ROOT}/scripts/install.py" --verify ${1+"$@"}
    ;;
  *)
    echo "Usage: board.sh [ensure|open|status|stop|rates|labels|verify] [args...]" >&2
    exit 2
    ;;
esac

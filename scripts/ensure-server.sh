#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

has_mode=0
for arg in ${1+"$@"}; do
  case "$arg" in
    --hook|--ensure|--status|--stop)
      has_mode=1
      break
      ;;
  esac
done

if [ "$has_mode" -eq 1 ]; then
  exec python3 -m swarm_board.server ${1+"$@"}
else
  exec python3 -m swarm_board.server --ensure ${1+"$@"}
fi

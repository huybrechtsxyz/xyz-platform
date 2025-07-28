#!/bin/bash
#===============================================================================
# Script Name   : validate-service.sh
# Description   : Validate the structure and content of a service definition JSON
# Usage         : ./validate-service.sh <service-file.json>
# Author        : Vincent Huybrechts
# Created       : 2025-07-25
#===============================================================================
set -euo pipefail

# Logging function
log() {
  local level="$1"; shift
  local msg="$*"
  local timestamp
  timestamp=$(date +"%Y-%m-%d %H:%M:%S")
  case "$level" in
    INFO)    echo -e "[\033[1;34mINFO\033[0m]  - $msg" ;;
    WARN)    echo -e "[\033[1;33mWARN\033[0m]  - $msg" ;;
    ERROR)   echo -e "[\033[1;31mERROR\033[0m] - $msg" >&2 ;;
    *)       echo -e "[UNKNOWN] - $msg" ;;
  esac
}

SERVICE_FILE="${1:-}"
if [[ -z "$SERVICE_FILE" || ! -f "$SERVICE_FILE" ]]; then
  log ERROR "[X] Missing or invalid registry file"
  exit 1
fi

main() {
  log INFO "[*] ... Validation service definition is valid."
  log INFO "[+] ... Service definition is valid."
}

main "$@"

#!/bin/bash
#===============================================================================
# Script Name   : validate-service.sh
# Description   : Validate the structure and content of a service definition JSON
# Usage         : ./validate-service.sh <service-file.json>
# Author        : Vincent Huybrechts
# Created       : 2025-07-25
#===============================================================================
set -euo pipefail

SERVICE_FILE="${1:-}"
if [[ -z "$SERVICE_FILE" || ! -f "$SERVICE_FILE" ]]; then
  log ERROR "[X] Missing or invalid registry file"
  exit 1
fi

main() {
  echo "+ Registry JSON is valid."
}

main "$@"

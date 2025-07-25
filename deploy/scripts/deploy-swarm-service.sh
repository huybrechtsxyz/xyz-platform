#!/bin/bash
#===============================================================================
# Script Name   : deploy-swarm-service.sh
# Description   : Pipeline code to call remote service deployment code
# Usage         : ./deploy-swarm-service.sh
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

: "${VAR_WORKSPACE:?Missing WORKSPACE env var}"
: "${VAR_ENVIRONMENT:?Missing ENVIRONMENT env var}"
: "${VAR_REGISTRYINFO:?Missing REGISTRYINFO env var}"
: "${VAR_TERRAFORM:?Missing TERRAFORM env var}"
: "${VAR_PATH_TEMP:?Missing PATH_TEMP env var}"

source "$(dirname "${BASH_SOURCE[0]}")/utilities.sh"

# Get the current service id and state
SERVICE_ID=$(jq -r '.service.id' <<< "$VAR_REGISTRYINFO")
if [[ -z "$SERVICE_ID" ]]; then
  echo "Error: SERVICE_ID is null or missing"
  exit 1
fi

SERVICE_STATE=$(jq -r '.service.state' <<< "$VAR_REGISTRYINFO")
if [[ ! "$SERVICE_STATE" =~ ^(enabled|disabled|removed)$ ]]; then
  log ERROR "[X] Invalid service state: '$SERVICE_STATE'. Must be one of: enabled, disabled, removed."
  exit 1
fi


main() {

}

main
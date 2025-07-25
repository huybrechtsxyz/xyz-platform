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

WORKSPACE_FILE=$(get_workspace_file "./deploy/workspaces" "$VAR_WORKSPACE") || exit 1
log INFO "[*] Getting workspace file: $WORKSPACE_FILE"

MANAGER_ID=$(get_manager_id "$WORKSPACE_FILE") || exit 1
log INFO "[*] Getting manager label: $MANAGER_ID"

# Determine the deploy path for scripts and service
PATH_DEPLOY="$VAR_PATH_TEMP/.deploy"
log INFO "[*] Deployment path: $PATH_DEPLOY"

PATH_CONFIG="$VAR_PATH_TEMP/$SERVICE_ID/config"
log INFO "[*] Configuration path: $PATH_CONFIG"

PATH_DOCS="$VAR_PATH_TEMP/$SERVICE_ID/docs"
log INFO "[*] Configuration path: $PATH_CONFIG"

SERVICE_PATH="./service/$SERVICE_ID"
log INFO "[*] Service path: $SERVICE_PATH"

enable_service() {
  log INFO "[*] Disabling service $SERVICE_ID..."
  # Add logic to stop or disable the service here
  log INFO "[*] Disabling service $SERVICE_ID...DONE"
}

disable_service() {
  log INFO "[*] Disabling service $SERVICE_ID..."
  # Add logic to stop or disable the service here
  log INFO "[*] Disabling service $SERVICE_ID...DONE"
}

remove_service() {
  log INFO "[*] Removing service $SERVICE_ID..."
  # Add logic to clean up/remove service artifacts here
  log INFO "[*] Removing service $SERVICE_ID...DONE"
}

main() {
  log INFO "[*] Deploying service $SERVICE_ID..."

  case "$SERVICE_STATE" in
    enabled)
      log INFO "[*] Service state is: $SERVICE_STATE"
      enable_service
      ;;
    disabled)
      log INFO "[*] Service state is: $SERVICE_STATE"
      disable_service
      ;;
    removed)
      log INFO "[*] Service state is: $SERVICE_STATE"
      remove_service
      ;;
    *)
      log ERROR "[X] Invalid service state: '$SERVICE_STATE'. Must be one of: enabled, disabled, removed."
      exit 1
      ;;
  esac

  log INFO "[+] Deploying service $SERVICE_ID...DONE"
}

main

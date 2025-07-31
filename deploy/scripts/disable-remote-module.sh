#!/bin/bash
#===============================================================================
# Script Name   : disable-remote-service.sh
# Description   : Pipeline code to call remote deployment code
# Usage         : ./disable-remote-service.sh <PATH_DEPLOY>
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
# Available directories and files
# |- $PATH_WORKSPACE/variables.env (Contains PATH_CONFIG|DOCS)
# |- $PATH_WORKSPACE/secrets.env
# |- $PATH_WORKSPACE/terraform.json
# |- $PATH_WORKSPACE/workspace.json
# |- $PATH_DEPLOY/registry.json
# |- $PATH_DEPLOY/service.json (includes paths[] )
#===============================================================================
set -eo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

# Resolve absolute path to the directory of this script
PATH_WORKSPACE="${1:-}"
: "${PATH_WORKSPACE:?Missing PATH_WORKSPACE}"
if [[ ! -d "$PATH_WORKSPACE" ]]; then
  echo "ERROR: Deployment path $PATH_WORKSPACE does not exist."
  exit 1
fi

# Sourcing variables and scripts
if [[ -f "$PATH_WORKSPACE/variables.env" ]]; then
  set -a
  source "$PATH_WORKSPACE/variables.env"
  set +a
else
  log ERROR "[X] Missing variables.env at $PATH_WORKSPACE"
  exit 1
fi

if [[ -f "$PATH_WORKSPACE/utilities.sh" ]]; then
  source "$PATH_WORKSPACE/utilities.sh"
else
  log ERROR "[X] Missing utilities.sh at $PATH_WORKSPACE"
  exit 1
fi

# PATH_SERVICE -> service temp dir in variables.env
# PATH_DEPLOY  -> service deploy dir in variables.env
# Capture the server's hostname
HOSTNAME=$(hostname)

# Should be in variables.env
# VAR_PATH_SERVICE

log INFO "[*] Workspace path  : $PATH_WORKSPACE"
log INFO "[*] Service path    : $PATH_SERVICE"
log INFO "[*] Registry path   : $PATH_DEPLOY"
log INFO "[*] Running on host : $HOSTNAME"

# Get the registry and service file
REGISTRY_FILE=$(get_registry_file "$PATH_DEPLOY")
XXXXXXXXXXXXXXXXX
SERVICE_FILE=$(get_service_file "$PATH_DEPLOY")
XXXXXXXXXXXXXXXXX

# Validate if registry and service id match
REGISTRY_ID=$(jq -r '.service.id' "$REGISTRY_FILE")
SERVICE_ID=$(jq -r '.service.id' "$SERVICE_FILE")
if [[ "$REGISTRY_ID" != "$SERVICE_ID" ]]; then
  log ERROR "[X] Service ID and Registry ID do not match: $SERVICE_ID vs $REGISTRY_ID"
  exit 1
fi

# Get the workspace file
: "${WORKSPACE:?Missing WORKSPACE env var}"
WORKSPACE_FILE=$(get_workspace_file "$PATH_WORKSPACE" "$WORKSPACE") || exit 1
log INFO "[*] Getting workspace $WORKSPACE file: $WORKSPACE_FILE"

# Get the terraform file
TERRAFORM_FILE=$(get_terraform_file "$PATH_WORKSPACE") || exit 1
log INFO "[*] Getting terraform file $TERRAFORM_FILE"

# Get the server id
MANAGER_ID=$(get_manager_id "$WORKSPACE_FILE") || exit 1
log INFO "[*] Getting manager label: $MANAGER_ID"

# Check if we are indeed on the server
SERVER_NAME=$(get_terraform_data "$TERRAFORM_FILE" "$MANAGER_ID" "name")
if [[ "$SERVER_NAME" != "$HOSTNAME" ]]; then
  log ERROR "[X] Service Name and Hostname do not match: $SERVER_NAME vs $HOSTNAME"
  exit 1
fi

main() {
  log INFO "[*] Deploying service: $SERVICE_ID..."
  
  # Check. If state is enabled.
  # Check. If state is disabled.

  # Stop the service

  # Remove docker secrets for service
  # remove_docker_secrets "$PATH_DEPLOY/secrets.env" || {
  #   log ERROR "[X] Error loading docker secrets for $SERVICE_ID"
  #   exit 1
  # }
  # safe_rm_rf "$PATH_DEPLOY/secrets.env"

  log INFO "[*] Deploying service: $SERVICE_ID...DONE"
}

main

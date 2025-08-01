#!/bin/bash
#===============================================================================
# Script Name   : remove-remote-service.sh
# Description   : Pipeline code to call remote deployment code
# Usage         : ./remove-remote-service.sh <PATH_DEPLOY>
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
# Available directories and files
# |- $PATH_WORKSPACE  - Contains the deploy folder
# |- $PATH_MODULE     - Module installpoint on the remote server
# |- $PATH_DEPLOY     - Contains the module files, service scripts, service config
#===============================================================================
set -eo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

#===============================================================================
# WORKSPACE
#===============================================================================

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

# Should be in variables.env
# VAR_PATH_SERVICE

log INFO "[*] Workspace path  : $PATH_WORKSPACE"
log INFO "[*] Module path     : $PATH_MODULE"
log INFO "[*] Deploy path     : $PATH_DEPLOY"
log INFO "[*] Running on host : $HOSTNAME"

#===============================================================================
# MODULE
#===============================================================================

if [[ ! -d "$PATH_MODULE" ]]; then
  echo "ERROR: Module path $PATH_MODULE does not exist."
  exit 1
fi

if [[ ! -d "$PATH_DEPLOY" ]]; then
  echo "ERROR: Module deployment path $PATH_DEPLOY does not exist."
  exit 1
fi

MODULE_FILE="$PATH_DEPLOY/module.json"
if [[ ! -f "$MODULE_FILE" ]]; then
  echo "ERROR: Module deployment file $MODULE_FILE does not exist."
  exit 1
fi

MODULE_ID=$(jq -r '.module.id' "$MODULE_FILE")
MODULE_CONFIG=$(jq -r '.module.config' "$MODULE_FILE")

#===============================================================================
# SERVICE
#===============================================================================

SERVICE_FILE="$PATH_MODULE/$MODULE_CONFIG"
if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "ERROR: Service file $SERVICE_FILE does not exist."
  exit 1
fi

#===============================================================================

main() {
  log INFO "[*] Deploying service: $SERVICE_ID..."
  
  # Check. If state is enabled.
  # Check. If state is disabled.
  
  # Remove docker secrets for service
  # remove_docker_secrets "$PATH_DEPLOY/secrets.env" || {
  #   log ERROR "[X] Error loading docker secrets for $SERVICE_ID"
  #   exit 1
  # }
  # safe_rm_rf "$PATH_DEPLOY/secrets.env"

  log INFO "[*] Deploying service: $SERVICE_ID...DONE"
}

main

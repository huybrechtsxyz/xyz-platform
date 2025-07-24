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

# Get the current service id
SERVICE_ID=$(jq -r '.service.id' <<< "$VAR_REGISTRYINFO")
if [[ -z "$SERVICE_ID" ]]; then
  echo "Error: SERVICE_ID is null or missing"
  exit 1
fi

# Get the remote IP
WORKSPACE_FILE=$(get_workspace_file "./deploy/workspaces" "$VAR_WORKSPACE") || exit 1
log INFO "[*] Getting workspace file: $WORKSPACE_FILE"

MANAGER_ID=$(get_manager_id "$WORKSPACE_FILE") || exit 1
log INFO "[*] Getting manager label: $MANAGER_ID"

REMOTE_IP=$(echo "$VAR_TERRAFORM" | \
  jq -r \
  --arg label "$MANAGER_ID" \
  '.include[] | select(.label == $label) | .ip') || exit 1

log INFO "[*] Getting management IP for server: $REMOTE_IP"

if [[ ! "$REMOTE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  log ERROR "[X] Invalid IP address format: $REMOTE_IP"
  exit 1
fi

# Determine the deploy path for scripts and service
PATH_DEPLOY="$VAR_PATH_TEMP/.deploy"
log INFO "[*] Deployment path: $PATH_DEPLOY"

PATH_CONFIG="$VAR_PATH_TEMP/$SERVICE_ID"
log INFO "[*] Configuration path: $PATH_CONFIG"

SERVICE_PATH="./service/$SERVICE_ID"
log INFO "[*] Service path: $SERVICE_PATH"

# Create secret and variable files based on expected prefixes
# Output files added to deploy folder
# |- ./deploy/variables.env   (VAR_)
# |- ./deploy/secrets.env     (SECRET_)
# |- ./deploy/workspace.json
# |- ./deploy/terraform.json
# |- SERVICE_PATH/registry.json
create_environment_files() {
  # Terraform file is already on the server
  echo "$VAR_TERRAFORM" > "./deploy/terraform.json"
  unset VAR_TERRAFORM
  # Create variables and secret files
  generate_env_file "VAR_" "./deploy/variables.env"
  generate_env_file "SECRET_" "./deploy/secrets.env"
  # Store the registry info on the service
  echo "$VAR_REGISTRYINFO" > "$SERVICE_PATH/registry.json"
  unset VAR_REGISTRYINFO
}

# Copy configuration files to the remote server by creating necessary directories
# Creates the temporary application path and subdirectories on the remote server
copy_configuration_files() {
log INFO "[*] Copying service files to $REMOTE_IP..."
shopt -s nullglob

if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
set -e
mkdir -p "$VAR_PATH_TEMP" "$PATH_DEPLOY" "$PATH_CONFIG"
EOF
then
log ERROR "[X] Copying service files failed to $REMOTE_IP"
exit 1
fi

log INFO "[*] Copying deployment scripts to remote server...Deploy"
scp -o StrictHostKeyChecking=no \
  ./deploy/*.* \
  ./deploy/scripts/*.* \
  ./deploy/workspaces/*.* \
  root@"$REMOTE_IP":"$PATH_DEPLOY"/ || {
    log ERROR "[x] Failed to transfer deployment scripts to remote server"
    exit 1
  }

log INFO "[*] Copying configuration files to remote server...Service"
scp -o StrictHostKeyChecking=no \
  $SERVICE_PATH/config/* \
  $SERVICE_PATH/scripts/* \
  root@"$REMOTE_IP":"$PATH_CONFIG"/ || {
    log ERROR "[x] Failed to transfer service files to remote server"
    exit 1
  }

log INFO "[*] Debugging deployment path of remote server..."
ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
  ls -la "$PATH_DEPLOY"
  ls -la "$PATH_CONFIG"
EOF

log INFO "[*] Copying service files to $REMOTE_IP...DONE"
}

# Deploys the remote service by executing the script
# This script is executed on the remote server to set up the service
# The script is executed in a non-interactive SSH session
configure_service() {
log INFO "[*] Executing REMOTE deployment..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
chmod +x "$PATH_DEPLOY/deploy-remote-service.sh"
"$PATH_DEPLOY/deploy-remote-service.sh" "$PATH_CONFIG"
EOF
then
log ERROR "[X] Remote deployment failed on $REMOTE_IP"
exit 1
fi
log INFO "[*] Executing on REMOTE server...DONE"
}

main() {
  log INFO "[*] Deploying service $SERVICE_ID..."

  # Create variable file for the service
  if ! create_environment_files; then
    log ERROR "[X] Failed to create environment file."
    exit 1
  fi

  if ! copy_configuration_files; then
    log ERROR "[X] Failed to copy configuration files to remote server"
    exit 1
  fi

  if ! configure_service; then
    log ERROR "[X] Failed to configure remote service"
    exit 1
  fi

  log INFO "[+] Deploying service $SERVICE_ID...DONE"
}

main
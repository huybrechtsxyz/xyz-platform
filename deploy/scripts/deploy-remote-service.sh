#!/bin/bash
#===============================================================================
# Script Name   : deploy-remote-service.sh
# Description   : Pipeline code to call remote deployment code
# Usage         : ./deploy-remote-service.sh <PATH_CONFIG>
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
# Available directories and files
# |- $PATH_DEPLOY/variables.env
# |- $PATH_DEPLOY/secrets.env
# |- $PATH_DEPLOY/terraform.json
# |- $PATH_DEPLOY/workspace.json
# |- $PATH_CONFIG/registry.json
#===============================================================================
set -eo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

# Resolve absolute path to the directory of this script
PATH_DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${PATH_DEPLOY:?Missing PATH_DEPLOY}"
if [[ ! -d "$PATH_DEPLOY" ]]; then
  echo "ERROR: Deployment path $PATH_DEPLOY does not exist."
  exit 1
fi

# Get and validate service configuration path (passed as first argument)
PATH_CONFIG="${1:-}"
: "${PATH_CONFIG:?Missing PATH_CONFIG (first script argument)}"
if [[ ! -d "$PATH_CONFIG" ]]; then
  echo "ERROR: Service path $PATH_CONFIG does not exist."
  exit 1
fi

# Sourcing variables and scripts
if [[ -f "$PATH_DEPLOY/variables.env" ]]; then
  source "$PATH_DEPLOY/variables.env"
else
  log ERROR "[X] Missing variables.env at $PATH_DEPLOY"
  exit 1
fi

if [[ -f "$PATH_DEPLOY/utilities.sh" ]]; then
  source "$PATH_DEPLOY/utilities.sh"
else
  log ERROR "[X] Missing utilities.sh at $PATH_DEPLOY"
  exit 1
fi

# Capture the server's hostname
HOSTNAME=$(hostname)

log INFO "[*] Deployment path : $PATH_DEPLOY"
log INFO "[*] Service path    : $PATH_CONFIG"
log INFO "[*] Running on host : $HOSTNAME"

# Get the registry and service file
REGISTRY_FILE="$PATH_CONFIG/registry.json"
SERVICE_FILE="$PATH_CONFIG/service.json"

REGISTRY_ID=$(jq -r '.service.id' "$REGISTRY_FILE")
SERVICE_ID=$(jq -r '.service.id' "$SERVICE_FILE")

if [[ "$REGISTRY_ID" != "$SERVICE_ID" ]]; then
  log ERROR "[X] Service ID and Registry ID do not match: $SERVICE_ID vs $REGISTRY_ID"
  exit 1
fi

# Get the workspace file
: "${WORKSPACE:?Missing WORKSPACE env var}"
WORKSPACE_FILE=$(get_workspace_file "$PATH_DEPLOY" "$WORKSPACE") || exit 1
log INFO "[*] Getting workspace $WORKSPACE file: $WORKSPACE_FILE"

# Get the terraform file
TERRAFORM_FILE=$(get_terraform_file "$PATH_DEPLOY") || exit 1
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

# Create the service by copying the correct files and creating the correct directories
create_service() {
  log INFO "[*] Starting service setup: $WORKSPACE on host $hostname"

  # Calculate the configuration path on the server
  # We are running on the manager-1 (the first server defined in the workspace file)
  local managerinfo=$(jq --arg serverid "$MANAGER_ID" -c '.servers[] | select(.id == $serverid)' "$WORKSPACE_FILE")
  local managermount=$(echo "$managerinfo" | jq -r '.mountpoint')
  local managerdisk=$(echo "$managerinfo" | jq --arg type "config" -c '.mounts[] | select(.type == $type) | .disk')
  local managerpath=$(echo "$managerinfo" | jq --arg type "config" -r '.paths[] | select(.type == $type) | .path')
  local managervolume=$(echo "$managerinfo" | jq --arg type "config" -r '.paths[] | select(.type == $type) | .volume')
  local configpath="${managermount//\$\{disk\}/$managerdisk}${managerpath}"

  # Volume can be local, replicated, distributed
  case "$managervolume" in
    local|replicated|distributed)
      ;;  # valid
    *)
      log ERROR "[X] Invalid volume type: $managervolume"
      exit 1
      ;;
  esac

  # On local copy to all servers BUT standard is replicated
  # Then we only copy to manager configpath
  if [[ "$managervolume" != "local" ]]; then
    # Create the service configuration path and copy files
    service_path="$configpath/$SERVICE_ID"
    mkdir -p "$service_path"
    cp "$PATH_CONFIG" "$service_path"
    
  fi








  # Get the servers from the terraform output
  mapfile -t servers < <(jq -c '.include[]' "$TERRAFORM_FILE")
  server_count=${#servers[@]}
  log INFO "[*] Terraform data loaded: $server_count servers found"

  # Get the paths from the workspace
  mapfile -t paths < <(jq -c '.service.paths[]' "$WORKSPACE_FILE")
  path_count=${#paths[@]}
  log INFO "[*] Workspace data loaded: $paths_count paths found"
  
  
  
  # For each server (private-ip)
  
  first=true
  for serverinfo in "${servers[@]}"; do
    # Get server information
    local server_id=$(echo "$serverinfo" | jq -r '.label')
    local private_ip=$(echo "$serverinfo" | jq -r '.private_ip')

   
    first=false
  done

  log INFO "[+] Completed service setup: $WORKSPACE on host $hostname"
}

main() {
  log INFO "[*] Deploying service: $SERVICE_ID..."

  # Load docker secrets for service
  load_docker_secrets "$PATH_DEPLOY/secrets.env" || {
    log ERROR "[X] Error loading docker secrets for $SERVICE_ID"
    exit 1
  }
  safe_rm_rf "$PATH_DEPLOY/secrets.env"

  # Create the service workspace
  create_service || {
    log ERROR "[X] Error creating workspace for $SERVICE_ID"
    exit 1
  }

  log INFO "[*] Deploying service: $SERVICE_ID...DONE"
}

main

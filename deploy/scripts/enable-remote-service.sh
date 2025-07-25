#!/bin/bash
#===============================================================================
# Script Name   : enable-remote-service.sh
# Description   : Pipeline code to call remote deployment code
# Usage         : ./enable-remote-service.sh <PATH_DEPLOY>
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
# Available directories and files
# |- $PATH_DEPLOY/variables.env (Contains PATH_CONFIG|DOCS)
# |- $PATH_DEPLOY/secrets.env
# |- $PATH_DEPLOY/terraform.json
# |- $PATH_DEPLOY/workspace.json
# |- $PATH_CONFIG/registry.json
# |- $PATH_CONFIG/service.json (includes paths[] )
#===============================================================================
set -eo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

# Resolve absolute path to the directory of this script
PATH_DEPLOY="${1:-}"
: "${PATH_DEPLOY:?Missing PATH_DEPLOY}"
if [[ ! -d "$PATH_DEPLOY" ]]; then
  echo "ERROR: Deployment path $PATH_DEPLOY does not exist."
  exit 1
fi

# Sourcing variables and scripts
if [[ -f "$PATH_DEPLOY/variables.env" ]]; then
  set -a
  source "$PATH_DEPLOY/variables.env"
  set +a
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
log INFO "[*] Docs path       : $PATH_DOCS"
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

# Create the required service paths
create_remote_service_paths() {
  log INFO "[*] Creating remote service paths for: $SERVICE_ID"

  jq -c '.servers[]' "$WORKSPACE_FILE" | while read -r server; do
    local server_id=$(jq -r '.id' <<< "$server")
    local server_label="$server_id"
    local private_ip=$(jq -r --arg label "$server_label" '
      .include[] | select(.label == $label) | .private_ip
    ' "$TERRAFORM_FILE")

    if [[ -z "$private_ip" ]]; then
      log ERROR "[X] Could not find private IP for server label: $server_label"
      exit 1
    fi

    # Get the list of service paths for this server
    local path_list=$(jq -r --arg id "$server_id" '
      .servers[] | select(.id == $id) | .paths[]?.path
    ' "$WORKSPACE_FILE")

    if [[ -z "$path_list" ]]; then
      log WARN "[!] No service paths found for server: $server_id"
      continue
    fi

    # Build all mkdir commands into a single SSH execution
    log INFO "[*] Connecting to $private_ip to create paths..."
    commands=()

    while read -r path; do
      if [[ -n "$path" ]]; then
        commands+=("mkdir -p '$path'")
      fi
    done <<< "$path_list"


    if [[ "${#commands[@]}" -gt 0 ]]; then
      if [[ "$server_id" == "$MANAGER_ID" ]]; then
        # Run locally
        log INFO "[*] Executing locally (manager: $MANAGER_ID)..."
        for cmd in "${commands[@]}"; do
          eval "$cmd" || {
            log ERROR "[X] Failed to execute: $cmd"
            exit 1
          }
          log INFO "[✓] Executed: $cmd"
        done
      else
        # Run remotely over SSH
        log INFO "[*] Connecting to $private_ip for remote execution..."
        ssh -o StrictHostKeyChecking=no root@"$private_ip" "${commands[*]}" || {
          log ERROR "[X] Failed to create paths on server '$server_id' ($private_ip)"
          exit 1
        }
        log INFO "[✓] Created paths remotely on $server_id"
      fi
    else
      log WARN "[!] No paths to create for $server_id"
    fi
  done
}

create_service() {

  # Create the required service paths
  create_remote_service_paths



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

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

# Make certain the local temp path exists
if [[ ! -d "$VAR_PATH_TEMP" ]]; then
  echo "Temporary path $VAR_PATH_TEMP does not exist. Creating it."
  mkdir -p "$VAR_PATH_TEMP"
fi

# Get the workspace file and validate it
WORKSPACE_FILE=$(get_workspace_file "./deploy/workspaces" "$VAR_WORKSPACE") || exit 1
log INFO "[*] Getting workspace file: $WORKSPACE_FILE"
log INFO "[*] Validating workspace file: $WORKSPACE_FILE"
validate_workspace "./deploy/scripts" "$WORKSPACE_FILE"

# Get the manager-id from the workspace
MANAGER_ID=$(get_manager_id "$WORKSPACE_FILE") || exit 1
log INFO "[*] Getting manager label: $MANAGER_ID"

# Get the REMOTE IP for the MANAGER
REMOTE_IP=$(echo "$VAR_TERRAFORM" | \
  jq -r \
     --arg label "$MANAGER_ID" \
     '.include[] | select(.label == $label) | .ip') || exit 1
log INFO "[*] Getting management IP for server: $REMOTE_IP"
if [[ ! "$REMOTE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  log ERROR "[X] Invalid IP address format: $REMOTE_IP"
  exit 1
fi

# Terraform file is already on the server
echo "$VAR_TERRAFORM" > "./deploy/terraform.json"
unset VAR_TERRAFORM

# Get the service id
SERVICE_ID=$(jq -r '.service.id' <<< "$VAR_REGISTRYINFO")
if [[ -z "$SERVICE_ID" ]]; then
  echo "Error: SERVICE_ID is null or missing"
  exit 1
fi

# Get the service path
SERVICE_PATH="./service/$SERVICE_ID"

# Get the service state
SERVICE_STATE=$(jq -r '.service.state' <<< "$VAR_REGISTRYINFO")
if [[ ! "$SERVICE_STATE" =~ ^(enabled|disabled|removed)$ ]]; then
  log ERROR "[X] Invalid service state: '$SERVICE_STATE'. Must be one of: enabled, disabled, removed."
  exit 1
fi

# Get the service deployment path
REGISTRY_PATH=$(jq -r '.service.path' <<< "$VAR_REGISTRYINFO")
SERVICE_DEPLOY="$SERVICE_PATH/$REGISTRY_PATH"
if [[ ! -d "$SERVICE_DEPLOY" ]]; then
  echo "Temporary path $SERVICE_DEPLOY does not exist."
  exit 1
fi

# Validate if registry and service id match
REGISTRY_ID=$(jq -r '.service.id' <<< "$VAR_REGISTRYINFO")
if [[ "$REGISTRY_ID" != "$SERVICE_ID" ]]; then
  log ERROR "[X] Service ID and Registry ID do not match: $SERVICE_ID vs $REGISTRY_ID"
  exit 1
fi

# Save the registry info in the service deploy path
echo "$VAR_REGISTRYINFO" > "$SERVICE_DEPLOY/registry.json"
REGISTRY_FILE=$(get_registry_file "$SERVICE_DEPLOY") || exit 1
log INFO "[*] Validating registry file: $REGISTRY_FILE"
validate_registry "./deploy/scripts" "$REGISTRY_FILE"
unset VAR_REGISTRYINFO

# Add service paths per server
SERVICE_FILE=$(get_service_file "$SERVICE_DEPLOY")
log INFO "[*] Getting service file: $SERVICE_FILE"
log INFO "[*] Validating service file: $SERVICE_FILE"
validate_service "./deploy/scripts" "$SERVICE_FILE"

# Add the servicepaths to the service file and overwrite
log INFO "[*] Set the service paths in: $SERVICE_FILE as $VAR_PATH_TEMP/service.json"
create_service_serverpaths "$WORKSPACE_FILE" "$SERVICE_FILE" > "$VAR_PATH_TEMP/service.json"
cp -f "$VAR_PATH_TEMP/service.json" "$SERVICE_FILE"
rm -f "$VAR_PATH_TEMP/service.json"

# Get all the source and target paths
mapfile -t SERVICEPATHS < <( jq -c --arg server_id "$MANAGER_ID" \
  '.service.paths | map(select(.source != "" and .serverid == $server_id)) | .[]' \
  "$SERVICE_FILE")

# Get the REMOTE deployment paths
VAR_PATH_WORKSPACE="$VAR_PATH_TEMP/.deploy"
log INFO "[*] Remote workspace path: $VAR_PATH_WORKSPACE"

VAR_PATH_SERVICE="$VAR_PATH_TEMP/$SERVICE_ID"
log INFO "[*] Remote service path: $VAR_PATH_SERVICE"

VAR_PATH_DEPLOY="$VAR_PATH_SERVICE/$REGISTRY_PATH"
log INFO "[*] Remote deployment path: $VAR_PATH_DEPLOY"

# Create secret and variable files based on expected prefixes
# Output files added to deploy folder
# |- ./deploy/variables.env   (VAR_)
# |- ./deploy/secrets.env     (SECRET_)
create_environment_files() {
  # Create variables and secret files
  generate_env_file "VAR_" "./deploy/variables.env"
  generate_env_file "SECRET_" "./deploy/secrets.env"
}

# Function will lookup all service paths to copy
# Standard service path > gets the scripts
# Builds a list of .source pahts
# Creates them on the remote ip and copies relevant service paths
copy_service_files() {
  log INFO "[*] Copying service files to $REMOTE_IP..."
  local mkdir_cmds=()

  # Ensure base temp service path is included
  mkdir_cmds+=("mkdir -p '$VAR_PATH_SERVICE'")
  mkdir_cmds+=("mkdir -p '$VAR_PATH_DEPLOY'")

  # Build mkdir commands for each source subdirectory
  for item in "${SERVICEPATHS[@]}"; do
    local source=$(jq -r '.source' <<< "$item")
    [[ -z "$source" ]] && continue
    mkdir_cmds+=("mkdir -p '$VAR_PATH_SERVICE/$source'")
  done

  # Execute mkdirs in a single SSH command
  if [[ ${#mkdir_cmds[@]} -gt 0 ]]; then
    log INFO "[*] Creating service paths on $REMOTE_IP..."
    ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" "${mkdir_cmds[*]}" || {
      log ERROR "[X] Failed to create service directories on $REMOTE_IP"
      exit 1
    }
    log INFO "[+] All required directories created on $REMOTE_IP"
  fi

  # Copy shared deployment assets
  log INFO "[*] Copying deployment scripts..."
  scp -o StrictHostKeyChecking=no \
    ./deploy/*.* \
    ./deploy/scripts/*.* \
    ./deploy/workspaces/*.* \
    root@"$REMOTE_IP":"$VAR_PATH_WORKSPACE"/ || {
      log ERROR "[X] Failed to transfer core deployment scripts to $REMOTE_IP"
      exit 1
    }

  # Copy all paths with defined `source` folders
  for item in "${servicepaths[@]}"; do
    local source=$(jq -r '.source' <<< "$item")
    [[ -z "$source" ]] && continue
    local source_path="./service/$SERVICE_ID/$source"
    if [[ -d "$source_path" ]]; then
      log INFO "[*] Copying service source path '$source' to $REMOTE_IP..."
      scp -r -o StrictHostKeyChecking=no
        "$source_path/"* \
        root@"$REMOTE_IP":"$VAR_PATH_SERVICE/$source"/ || {
          log ERROR "[X] Failed to copy source folder '$source' to $REMOTE_IP"
          exit 1
        }
    else
      log WARN "[!] Source path '$source_path' not found — skipping"
    fi
  done

  # Copy service-specific scripts and files (if exist)
  log INFO "[*] Copying service-specific scripts..."
  scp -o StrictHostKeyChecking=no \
    ./service/"$SERVICE_ID"/*.* \
    ./service/"$SERVICE_ID"/scripts/*.* \
    root@"$REMOTE_IP":"$VAR_PATH_DEPLOY"/ || {
      log ERROR "[X] Failed to transfer service scripts to $REMOTE_IP"
      exit 1
    }

  log INFO "[*] Copying service files to $REMOTE_IP...DONE"
}

enable_service() {
log INFO "[*] Deploying service $SERVICE_ID..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
chmod +x "$VAR_PATH_DEPLOY/enable-remote-service.sh"
"$VAR_PATH_DEPLOY/enable-remote-service.sh" "$VAR_PATH_WORKSPACE"
EOF
then
log ERROR "[X] Remote deployment failed on $REMOTE_IP"
exit 1
fi
INFO "[*] Deploying service $SERVICE_ID...DONE"
}

disable_service() {
log INFO "[*] Disabling service $SERVICE_ID..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
chmod +x "$VAR_PATH_DEPLOY/disable-remote-service.sh"
"$VAR_PATH_DEPLOY/disable-remote-service.sh" "$VAR_PATH_WORKSPACE"
EOF
then
log ERROR "[X] Remote deployment failed on $REMOTE_IP"
exit 1
fi
log INFO "[*] Disabling service $SERVICE_ID...DONE"
}

remove_service() {
log INFO "[*] Removing service $SERVICE_ID..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
chmod +x "$VAR_PATH_DEPLOY/remove-remote-service.sh"
"$VAR_PATH_DEPLOY/remove-remote-service.sh" "$VAR_PATH_WORKSPACE"
EOF
then
log ERROR "[X] Remote deployment failed on $REMOTE_IP"
exit 1
fi
log INFO "[*] Removing service $SERVICE_ID...DONE"
}

main() {
  log INFO "[*] Deploying service $SERVICE_ID..."

  create_environment_files

  case "$SERVICE_STATE" in
    enabled)
      copy_service_files
      enable_service
      ;;
    disabled)
      disable_service
      ;;
    removed)
      remove_service
      ;;
    *)
      log ERROR "[X] Invalid service state: '$SERVICE_STATE'. Must be one of: enabled, disabled, removed."
      exit 1
      ;;
  esac
  log INFO "[*] Deploying service $SERVICE_ID...DONE"
}

main

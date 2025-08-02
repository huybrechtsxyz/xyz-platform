#!/bin/bash
#===============================================================================
# Script Name   : deploy-swarm-module.sh
# Description   : Pipeline code to call remote service deployment code
# Usage         : ./deploy-swarm-module.sh
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

: "${VAR_WORKSPACE:?Missing WORKSPACE env var}"
: "${VAR_MODULEINFO:?Missing VAR_MODULEINFO env var}"
: "${VAR_TERRAFORM:?Missing TERRAFORM env var}"
: "${VAR_PATH_TEMP:?Missing PATH_TEMP env var}"

source "$(dirname "${BASH_SOURCE[0]}")/utilities.sh"

# Make certain the local temp path exists
if [[ ! -d "$VAR_PATH_TEMP" ]]; then
  log INFO "[*] Temporary path $VAR_PATH_TEMP does not exist. Creating it."
  mkdir -p "$VAR_PATH_TEMP"
fi

#===============================================================================
# MODULE
#===============================================================================

# Get the module id
MODULE_ID=$(jq -r '.module.id' <<< "$VAR_MODULEINFO")
if [[ -z "$MODULE_ID" ]]; then
  log ERROR "[X] MODULE_ID is null or missing"
  exit 1
fi

MODULE_CONFIG=$(jq -r '.module.config' <<< "$VAR_MODULEINFO")
if [[ -z "$MODULE_CONFIG" ]]; then
  log ERROR "[X] MODULE_CONFIG is null or missing"
  exit 1
fi

MODULE_DEPLOY=$(jq -r '.module.deploy' <<< "$VAR_MODULEINFO")
if [[ -z "$MODULE_DEPLOY" ]]; then
  log ERROR "[X] MODULE_DEPLOY is null or missing"
  exit 1
fi

#===============================================================================
# WORKSPACE
#===============================================================================

# Get the workspace file and validate it
WORKSPACE_FILE=$(get_workspace_file "./workspaces" "$VAR_WORKSPACE") || exit 1
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
# We deploy it to make it easier for module deployment
# This file is not kept !
echo "$VAR_TERRAFORM" > "./deploy/terraform.json"
unset VAR_TERRAFORM

# Get the module state
MODULE_STATE=$(jq --arg module_id $MODULE_ID -r '.workspace.modules[] | select(.id == "$module_id") | .' "$WORKSPACE_FILE")
if [[ ! "$MODULE_STATE" =~ ^(enabled|disabled|removed)$ ]]; then
  log ERROR "[X] Invalid service module state: '$MODULE_STATE'. Must be one of: enabled, disabled, removed."
  exit 1
fi




















#===============================================================================
# SERVICE
#===============================================================================

# Get the service path
SERVICE_PATH="./services/$MODULE_ID"

# Get the service file
SERVICE_FILE="$SERVICE_PATH/$MODULE_CONFIG"
log INFO "[*] Getting service file: $SERVICE_FILE"
if [[ ! -f "$SERVICE_FILE" ]]; then
  log ERROR "[X] Service file $SERVICE_FILE does not exist."
  exit 1
fi

log INFO "[*] Validating service file: $SERVICE_FILE"
validate_service "./deploy/scripts" "$SERVICE_FILE" "$VAR_WORKSPACE" "$VAR_ENVIRONMENT"

# Get the service deployment path
SERVICE_DEPLOY="$SERVICE_PATH/$MODULE_DEPLOY"
if [[ ! -d "$SERVICE_DEPLOY" ]]; then
  log ERROR "[X] Service deployment folder $SERVICE_DEPLOY does not exist."
  exit 1
fi

# Add the servicepaths to the service file and overwrite
log INFO "[*] Set the service paths in: $SERVICE_FILE as $VAR_PATH_TEMP/service.json"
create_service_serverpaths "$WORKSPACE_FILE" "$SERVICE_FILE" > "$VAR_PATH_TEMP/service.json"
cp -f "$VAR_PATH_TEMP/service.json" "$SERVICE_FILE"
rm -f "$VAR_PATH_TEMP/service.json"

# Save the registry info in the service deploy path
MODULE_FILE="$SERVICE_DEPLOY/module.json"
echo "$VAR_MODULEINFO" > "$MODULE_FILE"
log INFO "[*] Validating module file: $MODULE_FILE"
validate_module "./deploy/scripts" "$MODULE_FILE"
unset VAR_MODULEINFO

#===============================================================================
# DEPLOYMENT PATHS
#===============================================================================

# Get all the source and target paths
mapfile -t SERVICEPATHS < <( jq -c --arg server_id "$MANAGER_ID" \
  '.service.paths | map(select(.source != "" and .serverid == $server_id)) | .[]' \
  "$SERVICE_FILE")

# Get the REMOTE deployment paths
VAR_PATH_WORKSPACE="$VAR_PATH_TEMP/.deploy"
log INFO "[*] Remote workspace path: $VAR_PATH_WORKSPACE"

VAR_PATH_MODULE="$VAR_PATH_TEMP/$MODULE_ID"
log INFO "[*] Remote module path: $VAR_PATH_MODULE"

VAR_PATH_DEPLOY="$VAR_PATH_MODULE/$MODULE_DEPLOY"
log INFO "[*] Remote deployment path: $VAR_PATH_DEPLOY"

#===============================================================================

# Create secret and variable files based on expected prefixes
# Output files added to deploy folder
# |- ./deploy/variables.env   (VAR_)
# |- ./deploy/secrets.env     (SECRET_)
create_environment_files() {
  # Extract matching variables
  log INFO "[*] Creating variable file $VAR_PATH_DEPLOY/variables.env"
  mapfile -t var_lines < <(jq --arg env "$VAR_ENVIRONMENT" -r '.service.deploy[$env].variables[] | "\(.key) \(.value)"' "$SERVICE_FILE")

  # Loop over each variable entry
  for line in "${var_lines[@]}"; do
    read -r key value <<< "$line"
    export "VAR_${key}=$value"
    echo "Exported VAR_${key}"
  done
  
  # Generate variable file
  generate_env_file "VAR_" "$VAR_PATH_DEPLOY/variables.env"

  # Extract matching secrets
  log INFO "[*] Creating secret file ./deploy/secrets.env"
  mapfile -t secret_lines < <(jq --arg env "$VAR_ENVIRONMENT" -r '.service.deploy[$env].secrets[] | "\(.key) \(.source) \(.id)"' "$SERVICE_FILE")

  export BWS_ACCESS_TOKEN="${BWS_ACCESS_TOKEN:?Missing BWS_ACCESS_TOKEN environment variable}"

  SECRETS_ENV_FILE="./deploy/secrets.env"
  > "$SECRETS_ENV_FILE"

  for line in "${var_lines[@]}"; do
    read -r key source id <<< "$line"

    if [[ "$source" != "bitwarden" ]]; then
      log WARN "[!] Skipping unsupported secret source: $source"
      continue
    fi

    data=$(bws secret get "$id" --output json 2>/dev/null || true)

    if [[ -z "$data" ]]; then
      log ERROR "[!] Failed to fetch secret for $key (id: $id)"
      continue
    fi

    value=$(jq -r '.value' <<< "$data")
    #echo "$key=${value@Q}"
    export "SECRET_${key}=$value"
    echo "Exported SECRET_${key}"
  done

  # Generate secret file
  generate_env_file "SECRET_" "$SECRETS_ENV_FILE"
}

# Function will lookup all service paths to copy
# Standard service path > gets the scripts
# Builds a list of .source pahts
# Creates them on the remote ip and copies relevant service paths
copy_service_files() {
  log INFO "[*] Copying service files to $REMOTE_IP..."
  local mkdir_cmds=()

  # Ensure base temp service path is included
  mkdir_cmds+=("mkdir -p '$VAR_PATH_WORKSPACE'")
  mkdir_cmds+=("mkdir -p '$VAR_PATH_MODULE'")
  mkdir_cmds+=("mkdir -p '$VAR_PATH_DEPLOY'")

  # Build mkdir commands for each source subdirectory
  for item in "${SERVICEPATHS[@]}"; do
    local source=$(jq -r '.source' <<< "$item")
    [[ -z "$source" ]] && continue
    mkdir_cmds+=("mkdir -p '$VAR_PATH_MODULE/$source'")
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
    ./workspaces/*.* \
    root@"$REMOTE_IP":"$VAR_PATH_WORKSPACE"/ || {
      log ERROR "[X] Failed to transfer core deployment scripts to $REMOTE_IP"
      exit 1
    }

  # Copy all paths with defined `source` folders
  # Do not copy the when the "$source" equals "$MODULE_"
  for item in "${servicepaths[@]}"; do
    local source=$(jq -r '.source' <<< "$item")
    [[ -z "$source" ]] && continue
    [[ "$source" == "$MODULE_DEPLOY" ]] && continue
    local source_path="$SERVICE_PATH/$source"
    if [[ -d "$source_path" ]]; then
      log INFO "[*] Copying service source path '$source' to $REMOTE_IP..."
      scp -r -o StrictHostKeyChecking=no
        "$source_path/"* \
        root@"$REMOTE_IP":"$VAR_PATH_MODULE/$source"/ || {
          log ERROR "[X] Failed to copy source folder '$source' to $REMOTE_IP"
          exit 1
        }
    else
      log WARN "[!] Source path '$source_path' not found — skipping"
    fi
  done

  # Copy service-specific scripts and files (if exist)
  # Copy the deployment files to the root of the remote module
  log INFO "[*] Copying service-specific scripts..."
  scp -o StrictHostKeyChecking=no \
    "$SERVICE_PATH/$MODULE_DEPLOY"/*.* \
    "$SERVICE_PATH"/scripts/*.* \
    root@"$REMOTE_IP":"$VAR_PATH_DEPLOY"/ || {
      log ERROR "[X] Failed to transfer service scripts to $REMOTE_IP"
      exit 1
    }

  log INFO "[*] Copying service files to $REMOTE_IP...DONE"
}

enable_service() {
log INFO "[*] Deploying service $SERVICE_ID..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
chmod +x "$VAR_PATH_DEPLOY/*.sh"
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
chmod +x "$VAR_PATH_DEPLOY/*.sh"
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
chmod +x "$VAR_PATH_DEPLOY/*.sh"
"$VAR_PATH_DEPLOY/remove-remote-service.sh" "$VAR_PATH_WORKSPACE"
EOF
then
log ERROR "[X] Remote deployment failed on $REMOTE_IP"
exit 1
fi
log INFO "[*] Removing service $SERVICE_ID...DONE"
}

main() {
  log INFO "[*] Deploying service $MODULE_ID..."

  create_environment_files

  case "$MODULE_STATE" in
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
      log ERROR "[X] Invalid service state: '$MODULE_STATE'. Must be one of: enabled, disabled, removed."
      exit 1
      ;;
  esac
  log INFO "[*] Deploying service $MODULE_ID...DONE"
}

main

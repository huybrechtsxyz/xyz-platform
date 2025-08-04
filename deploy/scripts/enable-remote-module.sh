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
# |- $PATH_WORKSPACE  - Contains the deploy folder
# |- $PATH_MODULE     - Module installpoint on the remote server
# |- $PATH_DEPLOY     - Contains the module files, service scripts, service config
#===============================================================================
set -eo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

# Capture the server's hostname
HOSTNAME=$(hostname)

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

  # Log the content of variables.env
  log INFO "[*] Content of $PATH_WORKSPACE/variables.env:"
  while IFS= read -r line; do
    log INFO "[*] ... $line"
  done < "$PATH_WORKSPACE/variables.env"
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

#===============================================================================
# WORKSPACE
#===============================================================================

# Get the workspace file that was deployed with the calculated paths
WORKSPACE_FILE=$(get_workspace_file "$PATH_WORKSPACE" "$WORKSPACE")
if [[ ! -f "$WORKSPACE_FILE" ]]; then
  log ERROR "[X] Missing workspace definition at $WORKSPACE_FILE"
  exit 1
fi

# Get the terraform file
TERRAFORM_FILE=$(get_terraform_file "$PATH_WORKSPACE")
if [[ ! -f "$TERRAFORM_FILE" ]]; then
  log ERROR "[X] Missing terraform definition at $TERRAFORM_FILE"
  exit 1
fi
log INFO "[*] Getting terraform file $TERRAFORM_FILE"

# Get the server id
MANAGER_ID=$(get_server_id "$WORKSPACE_FILE" "$HOSTNAME") || exit 1
log INFO "[*] Getting workspace server id: $MANAGER_ID"

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
  log ERROR "[X] Missing module definition at $MODULE_FILE"
  exit 1
fi
log INFO "[*] Getting module file $MODULE_FILE"

MODULE_ID=$(jq -r '.module.id' "$MODULE_FILE")

#===============================================================================
# SERVICE
#===============================================================================

SERVICE_FILE="$PATH_DEPLOY/service.json"
if [[ ! -f "$SERVICE_FILE" ]]; then
  log ERROR "[X] Missing service definition at $SERVICE_FILE"
  exit 1
fi
log INFO "[*] Getting service file $SERVICE_FILE"

log INFO "[*] Installpoint path : $PATH_WORKSPACE"
log INFO "[*] Module path       : $PATH_MODULE"
log INFO "[*] Deploy path       : $PATH_DEPLOY"
log INFO "[*] Running on host   : $HOSTNAME"
log INFO "[*] Manager ID        : $MANAGER_ID"

#===============================================================================

deploy_service(){
  log INFO "[*] Creating remote service paths for: $MODULE_ID..."

  mapfile -t servers < <(jq -c '.servers[]' "$WORKSPACE_FILE")

  for serverdata in "${servers[@]}"; do
    serverid=$(jq -r '.id' <<< "$serverdata")
    privateip=$(jq -r --arg label "$serverid" '.include[] | select(.label == $label) | .private_ip' "$TERRAFORM_FILE")

    if [[ -z "$privateip" ]]; then
      log ERROR "[X] Could not find private IP for server label: $serverid"
      exit 1
    fi

    # Get full path metadata once
    mapfile -t modulepaths < <(jq -c --arg id "$serverid" '.service.paths[] | select(server.id == $id)' "$SERVICE_FILE")
    if (( ${modulepaths[@]} == 0 )); then
      log WARN "[!] No service paths found for server: $serverid"
      continue
    fi

    # Build mkdir commands
    commands=()
    for pathdata in "${modulepaths[@]}"; do
      path_target=$(jq -r '.path' <<< "$pathdata")
      [[ -n "$path_target" ]] && commands+=("mkdir -p '$path_target'")
    done

    # Create the directories
    if [[ "${#commands[@]}" -gt 0 ]]; then
      if [[ "$serverid" == "$MANAGER_ID" ]]; then
        log INFO "[*] ... Executing locally (manager: $MANAGER_ID)..."
        for cmd in "${commands[@]}"; do
          eval "$cmd" || {
            log ERROR "[X] ... Failed to execute: $cmd"
            exit 1
          }
          log INFO "[+] ... Executed: $cmd"
        done
      else
        log INFO "[*] ... Connecting to $privateip for remote execution..."
        ssh -o StrictHostKeyChecking=no root@"$privateip" "${commands[*]}" || {
          log ERROR "[X] Failed to create paths on server '$serverid' ($privateip)"
          exit 1
        }
        log INFO "[+] ... Created paths remotely on $server_id"
      fi
    fi

    # Copy files to server based on volume type
    for pathdata in "${modulepaths[@]}"; do
      path_type=$(jq -r '.type' <<< "$pathdata")
      path_target=$(jq -r '.path' <<< "$pathdata")
      path_volume=$(jq -r '.volume' <<< "$pathdata")
      path_source=$(jq -r '.source' <<< "$pathdata")
      source_path="$PATH_MODULE/$path_source"

      # Skip if there is no type source or target
      [[ -z "$path_source" || -z "$path_target" || -z "$path_type" || -z "$path_volume" ]] && continue

      # Skip if replicated/distributed and not manager
      if [[ "$path_volume" != "local" && "$serverid" != "$MANAGER_ID" ]]; then
        log INFO "[~] Skipping $path_type copy to $serverid (volume: $path_volume)"
        continue
      fi

      if [[ -d "$source_path" ]]; then
        if [[ "$serverid" != "$MANAGER_ID" ]]; then
          log INFO "[*] Copying $path_type files to $serverid:$path_target"
          scp -r -o StrictHostKeyChecking=no "$source_path/" root@"$privateip":"$path_target"/ || {
            log ERROR "[X] Failed to copy $path_type to $serverid"
            exit 1
          }
        else
          log INFO "[*] Copying $path_type files locally to $path_target"
          cp -rf "$source_path/" "$path_target"/ || {
            log ERROR "[X] Local copy to $path_target failed"
            exit 1
          }
        fi
        log INFO "[+] Copied $path_type files to $serverid"
      else
        log WARN "[!] Source path '$source_path' for $path_type does not exist, skipping"
      fi
    done

  done

  log INFO "[*] Creating remote service paths for: $MODULE_ID...DONE"
}

main() {
  log INFO "[*] Deploying service: $MODULE_ID..."

  # Load docker secrets for service
  load_docker_secrets "$PATH_DEPLOY/secrets.env" || {
    log ERROR "[X] Error loading docker secrets for $MODULE_ID"
    exit 1
  }
  safe_rm_rf "$PATH_DEPLOY/secrets.env"

  # Create the required service paths and copy files
  deploy_service || {
    log ERROR "[X] Error creating service paths for $MODULE_ID"
    exit 1
  }

  log INFO "[*] Deploying service: $MODULE_ID...DONE"
}

main

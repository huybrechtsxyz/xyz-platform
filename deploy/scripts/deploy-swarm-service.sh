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

# Determine the deploy path for scripts and service
export VAR_PATH_DEPLOY="$VAR_PATH_TEMP/.deploy"
log INFO "[*] Deployment path: $VAR_PATH_DEPLOY"

get_workspace_vars() {
  export WORKSPACE_FILE=$(get_workspace_file "./deploy/workspaces" "$VAR_WORKSPACE") || exit 1
  log INFO "[*] Getting workspace file: $WORKSPACE_FILE"
  log INFO "[*] Validating workspace file: $WORKSPACE_FILE"
  validate_workspace "./deploy/scripts" "$WORKSPACE_FILE"

  export MANAGER_ID=$(get_manager_id "$WORKSPACE_FILE") || exit 1
  log INFO "[*] Getting manager label: $MANAGER_ID"
}

get_terraform_vars() {
  REMOTE_IP=$(echo "$VAR_TERRAFORM" | \
    jq -r \
    --arg label "$MANAGER_ID" \
    '.include[] | select(.label == $label) | .ip') || exit 1
  log INFO "[*] Getting management IP for server: $REMOTE_IP"
  if [[ ! "$REMOTE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    log ERROR "[X] Invalid IP address format: $REMOTE_IP"
    exit 1
  fi
}

get_service_vars() {
  Get the current service id and state
  export SERVICE_ID=$(jq -r '.service.id' <<< "$VAR_REGISTRYINFO")
  if [[ -z "$SERVICE_ID" ]]; then
    echo "Error: SERVICE_ID is null or missing"
    exit 1
  fi

  export SERVICE_STATE=$(jq -r '.service.state' <<< "$VAR_REGISTRYINFO")
  if [[ ! "$SERVICE_STATE" =~ ^(enabled|disabled|removed)$ ]]; then
    log ERROR "[X] Invalid service state: '$SERVICE_STATE'. Must be one of: enabled, disabled, removed."
    exit 1
  fi

  export SERVICE_PATH="./service/$SERVICE_ID"
  log INFO "[*] Service path: $SERVICE_PATH"

  SERVICE_FILE=$(get_service_file "$SERVICE_PATH")
  log INFO "[*] Getting service file: $SERVICE_FILE"
  
  echo "$VAR_REGISTRYINFO" > "$SERVICE_PATH/registry.json"
  export REGISTRY_FILE=$(get_registry_file "$SERVICE_PATH") || exit 1
  log INFO "[*] Validating registry file: $REGISTRY_FILE"
  validate_registry "./deploy/scripts" "$REGISTRY_FILE"
}

get_servicepath_vars() {
  local copypaths=("$@")
  for pathdata in "${copypaths[@]}"; do
    name=$(jq -r '.name' <<< "$pathdata" | tr '[:lower:]' '[:upper:]' | tr -c 'A-Z0-9' '_')
    target=$(jq -r '.path' <<< "$pathdata")

    if [[ -n "$name" && -n "$target" ]]; then
      export "PATH_${name}"="$VAR_PATH_TEMP/${target}"
      log INFO "[~] Exported PATH_${name}=$VAR_PATH_TEMP/${target}"
    else
      log WARN "[!] Skipped invalid path export for: $name -> $target"
    fi
  done
}

# Create secret and variable files based on expected prefixes
# Output files added to deploy folder
# |- ./deploy/variables.env   (VAR_)
# |- ./deploy/secrets.env     (SECRET_)
# |- ./deploy/workspace.json
# |- ./deploy/terraform.json
# |- SERVICE_PATH/registry.json
# |- SERVICE_PATH/service.json (contains paths[])
create_environment_files() {
  # Terraform file is already on the server
  echo "$VAR_TERRAFORM" > "./deploy/terraform.json"
  unset VAR_TERRAFORM

  # Do not use the registry info (-./deploy/registry.json)
  unset VAR_REGISTRYINFO

  # Add the servicepaths to the service file and overwrite
  cp -f "$VAR_PATH_TEMP/service.json" "$SERVICE_FILE"
  rm -f "$VAR_PATH_TEMP/service.json"

  # Create variables and secret files
  generate_env_file "VAR_" "./deploy/variables.env"
  generate_env_file "SECRET_" "./deploy/secrets.env"
}

copy_service_files() {
  log INFO "[*] Copying service files to $REMOTE_IP..."
  shopt -s nullglob
  local copypaths=("$@")

  local mkdir_cmds=()
  local scp_cmds=()

  VAR_PATH_TEMP
  VAR_PATH_DEPLOY

  for item in "${paths[@]}"; do
    local path=$(jq -r '.path' <<< "$item")
    local source=$(jq -r '.source' <<< "$item")

    # Skip invalid
    [[ -z "$path" || -z "$source" ]] && continue

    local source_dir="$PATH_SERVICE/$source"
    [[ ! -d "$source_dir" ]] && {
      log WARN "[!] Source directory '$source_dir' does not exist, skipping"
      continue
    }

    mkdir_cmds+=("mkdir -p '$path'")
    scp_cmds+=("$source_dir $path")
  done

   # Create all paths in a single SSH session
  if [[ ${#mkdir_cmds[@]} -gt 0 ]]; then
    log INFO "[*] Creating paths on $server_ip..."
    ssh -o StrictHostKeyChecking=no root@"$server_ip" "${mkdir_cmds[*]}" || {
      log ERROR "[X] Failed to create paths on $server_ip"
      exit 1
    }
    log INFO "[✓] Created all paths on $server_ip"
  fi

  # Run all SCP copy operations
  for scp_pair in "${scp_cmds[@]}"; do
    local src=$(awk '{print $1}' <<< "$scp_pair")
    local dst=$(awk '{print $2}' <<< "$scp_pair")
    log INFO "[*] Copying $src to $server_ip:$dst"
    scp -r -o StrictHostKeyChecking=no "$src/" root@"$server_ip":"$dst"/ || {
      log ERROR "[X] Failed to copy $src to $dst"
      exit 1
    }
    log INFO "[✓] Copied $src → $dst"
  done

  log INFO "[*] Copying service files to $REMOTE_IP...DONE"
}

enable_service() {
log INFO "[*] Deploying service $SERVICE_ID..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
chmod +x "$VAR_PATH_DEPLOY/enable-remote-service.sh"
"$VAR_PATH_DEPLOY/enable-remote-service.sh" "$VAR_PATH_DEPLOY"
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
"$VAR_PATH_DEPLOY/disable-remote-service.sh" "$VAR_PATH_DEPLOY"
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
"$VAR_PATH_DEPLOY/remove-remote-service.sh" "$VAR_PATH_DEPLOY"
EOF
then
log ERROR "[X] Remote deployment failed on $REMOTE_IP"
exit 1
fi
log INFO "[*] Removing service $SERVICE_ID...DONE"
}

main() {
  log INFO "[*] Deploying service $SERVICE_ID..."
  get_workspace_vars
  get_terraform_vars
  get_service_vars

  # Add service paths per server
  create_service_serverpaths "$WORKSPACE_FILE" "$SERVICE_FILE" > "$VAR_PATH_TEMP/service.json"

  # Get all the source and target paths
  mapfile -t copypaths < <(
    jq -c --arg server_id "$MANAGER_ID" '
      .service.paths
      | map(select(.source != "" and .serverid == $server_id))
      | .[]
    ' "$VAR_PATH_TEMP/service.json"
  )

  # Export path variables
  if ! get_servicepath_vars "${copypaths[@]}"; then
    log ERROR "[X] Failed to create service path vars."
    exit 1
  fi

  if ! create_environment_files; then
    log ERROR "[X] Failed to create environment file."
    exit 1
  fi

  if ! copy_service_files "${copypaths[@]}"; then
    log ERROR "[X] Failed to copy configuration files to remote server"
    exit 1
  fi

  case "$SERVICE_STATE" in
    enabled)
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





























































# Copy configuration files to the remote server by creating necessary directories
# Creates the temporary application path and subdirectories on the remote server
copy_configuration_files() {
log INFO "[*] Copying service files to $REMOTE_IP..."
shopt -s nullglob

mapfile -t copypaths < <(
  jq -c --arg server_id "$MANAGER_ID" '
    .service.paths
    | map(select(.source != "" and .serverid == $server_id))
    | .[]
  ' "$SERVICE_FILE"
)

if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
set -e
mkdir -p "$VAR_PATH_TEMP" "$VAR_PATH_DEPLOY" "$VAR_PATH_CONFIG" "$VAR_PATH_DOCS"
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
  root@"$REMOTE_IP":"$VAR_PATH_DEPLOY"/ || {
    log ERROR "[x] Failed to transfer deployment scripts to remote server"
    exit 1
  }

if [[ -d "$SERVICE_PATH/config" || -d "$SERVICE_PATH/scripts" ]]; then
  log INFO "[*] Copying configuration files to remote server...Service"
  scp -o StrictHostKeyChecking=no \
    $SERVICE_PATH/config/* \
    $SERVICE_PATH/scripts/* \
    root@"$REMOTE_IP":"$VAR_PATH_CONFIG"/ || {
      log ERROR "[x] Failed to transfer service files to remote server"
      exit 1
    }
else
  log WARN "[!] Directory $SERVICE_PATH/config|scripts does not exist, skipping copy."
fi

if [[ -d "$SERVICE_PATH/docs" ]]; then
  log INFO "[*] Copying documentation files to remote server...Docs"
  scp -r -o StrictHostKeyChecking=no \
    $SERVICE_PATH/docs/ \
    root@"$REMOTE_IP":"$VAR_PATH_DOCS"/ || {
      log ERROR "[x] Failed to transfer documentation files to remote server"
      exit 1
    }
else
  log WARN "[!] Directory $SERVICE_PATH/docs does not exist, skipping copy."
fi

log INFO "[*] Debugging deployment path of remote server..."
ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
  ls -la "$VAR_PATH_DEPLOY"
  ls -la "$VAR_PATH_CONFIG"
  ls -la "$VAR_PATH_DOCS"
EOF

log INFO "[*] Copying service files to $REMOTE_IP...DONE"
}



main() {
  log INFO "[*] Deploying service $SERVICE_ID..."



  # Add the servicepaths to the service file
  create_service_serverpaths "$WORKSPACE_FILE" "$SERVICE_FILE" > "$VAR_PATH_TEMP/service.json"

  if ! create_environment_files; then
    log ERROR "[X] Failed to create environment file."
    exit 1
  fi

  case "$SERVICE_STATE" in
    enabled)
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

  log INFO "[+] Deploying service $SERVICE_ID...DONE"
}

main

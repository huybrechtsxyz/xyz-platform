#!/bin/bash
#===============================================================================
# Script Name   : configure-swarm-server.sh
# Description   : Pipeline code to call remote configuration code
# Usage         : ./configure-swarm-server.sh <REMOTE_IP>
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

# Get the remote IP and matrix from the arguments
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <REMOTE_IP>"
  exit 1
fi

# Get the input parameters into variables
REMOTE_IP="$1"
if [[ ! "$REMOTE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Invalid IP address format: $REMOTE_IP"
  exit 1
fi

# The deployment paths
: "${VAR_PATH_TEMP:="/tmp/app"}"
: "${VAR_MATRIX:?VAR_MATRIX is required}"

PATH_DEPLOY="$VAR_PATH_TEMP/.deploy"
PATH_CONFIG="$VAR_PATH_TEMP/.config"
PATH_DOCS="$VAR_PATH_TEMP/.docs"

# Source the utilities script for logging and environment variable handling
source "$(dirname "${BASH_SOURCE[0]}")/../../deploy/scripts/utilities.sh"

# Create secret and variable files based on expected prefixes
# Output files added to deploy folder
# |- ./deploy/configuration.env   (VAR_)
# |- ./deploy/secrets.env     (SECRET_)
# |- ./deploy/terraform.json  (TFOUTPUT)
create_environment_files() {
  log INFO "[*] Saving Terraform output to ./deploy/terraform.json"
  echo "$VAR_MATRIX" > "./deploy/terraform.json"
  unset VAR_MATRIX

  log INFO "[*] Creating workspace server paths"
  WORKSPACE_FILE=$(get_workspace_file "./workspaces" "$VAR_WORKSPACE")
  create_workspace_serverpaths "$WORKSPACE_FILE" > "./deploy/workspace.json"
  cp -f "./deploy/workspace.json" "$WORKSPACE_FILE"
  rm -f "./deploy/workspace.json"

  # Extract workspace variables
  log INFO "[*] Creating variable file ./deploy/configuration.env"
  mapfile -t var_lines < <(jq -r '.workspace.variables[] | "\(.key) \(.value)"' "$WORKSPACE_FILE")

  # Clear file before writing
  > ./deploy/configuration.env  

  for line in "${var_lines[@]}"; do
    read -r key value <<< "$line"
    #echo "$key=$value"
    export "VAR_${key}=$value"
    echo "Exported VAR_${key}"
  done

  generate_env_file "VAR_" "./deploy/configuration.env"

  # Extract secrets
  log INFO "[*] Creating secret file ./deploy/secrets.env"
  mapfile -t var_lines < <(jq -r '.workspace.secrets[] | "\(.key) \(.source) \(.id)"' "$WORKSPACE_FILE")

  export BWS_ACCESS_TOKEN="${BWS_ACCESS_TOKEN:?Missing BWS_ACCESS_TOKEN environment variable}"

  SECRETS_ENV_FILE="./deploy/secrets.env"
  > "$SECRETS_ENV_FILE"  # Clear existing file

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

  generate_env_file "SECRET_" "$SECRETS_ENV_FILE"
}

# Copy configuration files to the remote server by creating necessary directories
# Creates the temporary application path and subdirectories on the remote server
copy_configuration_files() {
  log INFO "[*] Copying configuration files to $REMOTE_IP..."
  shopt -s nullglob

if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
mkdir -p "$VAR_PATH_TEMP" "$PATH_DEPLOY" "$PATH_CONFIG" "$PATH_DOCS"
chmod 777 "$VAR_PATH_TEMP"
chmod 777 "$PATH_DEPLOY"
chmod 777 "$PATH_CONFIG"
chmod 777 "$PATH_DOCS"
EOF
then
log ERROR "[X] Copying configuration failed to $REMOTE_IP"
exit 1
fi

log INFO "[*] Copying deployment scripts to remote server...Deploy"
scp -o StrictHostKeyChecking=no \
  ./deploy/scripts/*.* \
  root@"$REMOTE_IP":"$PATH_DEPLOY"/ || {
    log ERROR "[x] Failed to transfer deployment scripts to remote server"
    exit 1
  }

log INFO "[*] Copying configuration files to remote server...Sources"
scp -o StrictHostKeyChecking=no \
  ./workspaces/*.* \
  ./deploy/*.* \
  ./scripts/*.sh \
  root@"$REMOTE_IP":"$PATH_CONFIG"/ || {
    log ERROR "[x] Failed to transfer configuration files to remote server"
    exit 1
  }

log INFO "[*] Copying documentation files to remote server...Docs"
scp -r -o StrictHostKeyChecking=no \
  ./docs/* \
  root@"$REMOTE_IP":"$PATH_DOCS"/ || {
    log ERROR "[x] Failed to transfer documentation files to remote server"
    exit 1
  }

log INFO "[*] Debugging deployment path of remote server..."
ssh -o StrictHostKeyChecking=no root@$REMOTE_IP << EOF
  ls -la "$PATH_DEPLOY"
  ls -la "$PATH_CONFIG"
  ls -la "$PATH_DOCS"
EOF

  log INFO "[*] Copying configuration files to $REMOTE_IP...DONE"
}

# Configures the remote server by executing the configuration script
# This script is executed on the remote server to set up the environment
# It sources the necessary environment files and runs the configuration script
# The script is executed in a non-interactive SSH session
execute_configuration() {
log INFO "[*] Executing REMOTE configuration..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
  chmod +x "$PATH_DEPLOY/configure-remote-server.sh"
  chmod +x "$PATH_DEPLOY/validate-workspace.sh"
  "$PATH_DEPLOY/configure-remote-server.sh" "$VAR_PATH_TEMP"
EOF
then
  log ERROR "[X] Remote configuration failed on $REMOTE_IP"
  exit 1
fi
log INFO "[*] Executing on REMOTE server...DONE"
}

main() {
  log INFO "[*] Configuring remote server at $REMOTE_IP..."

  if ! create_environment_files; then
    log ERROR "[X] Failed to create environment files."
    exit 1
  fi

  if ! copy_configuration_files; then
    log ERROR "[X] Failed to copy configuration files to remote server"
    exit 1
  fi

  if ! execute_configuration; then
    log ERROR "[X] Failed to configure remote server"
    exit 1
  fi

  log INFO "[+] Configuring remote server at $REMOTE_IP...DONE"
}

main

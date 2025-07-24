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

# Source the utilities script for logging and environment variable handling
source "$(dirname "${BASH_SOURCE[0]}")/../../deploy/scripts/utilities.sh"

# Create secret and variable files based on expected prefixes
# Output files added to deploy folder
# |- ./deploy/variables.env   (VAR_)
# |- ./deploy/secrets.env     (SECRET_)
# |- ./deploy/terraform.json  (TFOUTPUT)
create_environment_files() {
  echo "$VAR_MATRIX" > "./deploy/terraform.json"
  unset "$VAR_MATRIX"
  generate_env_file "VAR_" "./deploy/variables.env"
  generate_env_file "SECRET_" "./deploy/secrets.env"
}

# Copy configuration files to the remote server by creating necessary directories
# Creates the temporary application path and subdirectories on the remote server
copy_configuration_files() {
  log INFO "[*] Copying configuration files to $REMOTE_IP..."
  shopt -s nullglob

if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
mkdir -p "$VAR_PATH_TEMP" "$PATH_DEPLOY" "$PATH_CONFIG"
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
  ./deploy/workspaces/*.* \
  ./deploy/*.* \
  ./scripts/*.sh \
  root@"$REMOTE_IP":"$PATH_CONFIG"/ || {
    log ERROR "[x] Failed to transfer configuration files to remote server"
    exit 1
  }

log INFO "[*] Debugging deployment path of remote server..."
ssh -o StrictHostKeyChecking=no root@$REMOTE_IP << EOF
  ls -la "$PATH_DEPLOY"
  ls -la "$PATH_CONFIG"
EOF

  log INFO "[*] Copying configuration files to $REMOTE_IP...DONE"
}

# Configures the remote server by executing the configuration script
# This script is executed on the remote server to set up the environment
# It sources the necessary environment files and runs the configuration script
# The script is executed in a non-interactive SSH session
configure_server() {
log INFO "[*] Executing REMOTE configuration..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
chmod +x "$PATH_DEPLOY/configure-remote-server.sh"
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

  if ! configure_server; then
    log ERROR "[X] Failed to configure remote server"
    exit 1
  fi

  log INFO "[+] Configuring remote server at $REMOTE_IP...DONE"
}

main

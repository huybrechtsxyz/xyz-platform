#!/bin/bash
#===============================================================================
# Script Name   : run_initialization.sh
# Description   : 
# Usage         : ./run_initialization.sh <MATRIX - NAME>
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-05
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: `$BASH_COMMAND`"' ERR

if ! command -v yq &> /dev/null; then
  echo "[X] ERROR yq is required but not installed."
  exit 1
fi

SERVER_NAME=$1
: "${SERVER_NAME:?ERROR SERVER_NAME variable is not set.}"
: "${WORKSPACE_NAME:?ERROR WORKSPACE_NAME variable is not set.}"
: "${WORKSPACE_FILE:?ERROR WORKSPACE_FILE variable is not set.}"
: "${TERRAFORM_FILE:?ERROR TERRAFORM_FILE variable is not set.}"

SCRIPT_DIR="./deploy/scripts"
TEMPLATE_DIR="templates"
WORKSPACE_DIR="workspaces"
WORKSPACE_FILE="./$WORKSPACE_FILE"

# Load script utilities
source "$SCRIPT_DIR/utilities.sh"
load_script "$SCRIPT_DIR/utilities.sh"
load_script "$SCRIPT_DIR/use_workspace.sh"
load_script "$SCRIPT_DIR/use_terraform.sh"

# Load workspace and terraform data
TF_DATA=$(get_tf_data "$TERRAFORM_FILE")
VM_DATA=$(get_tf_server_by_name "$TF_DATA")
REMOTE_IP=$(get_tf_vm_publicip "$VM_DATA")
RESX_NAME=$(get_tf_vm_resourceid "$VM_DATA")

WS_DATA=$(get_ws_data "$WORKSPACE_NAME" "$WORKSPACE_FILE")
RESX_DATA=$(get_ws_resx_from_name "$RESX_NAME" "$WS_DATA")
RESX_INSTALL=$(get_ws_resx_installpoint "$WS_DATA")

log INFO "[*] WORKSPACE_FILE: WORKSPACE_FILE"
log INFO "[*] TF_DATA: TF_DATA"
log INFO "[*] VM_DATA: VM_DATA"
log INFO "[*] REMOTE_IP: REMOTE_IP"
log INFO "[*] RESX_NAME: RESX_NAME"
log INFO "[*] WS_DATA: WS_DATA"
log INFO "[*] RESX_DATA: RESX_DATA"
log INFO "[*] RESX_INSTALL: RESX_INSTALL"

exit 0

# Create a temporary directory for the initialization scripts
create_environment_files() {
  log INFO "[*] Creating environment file for workspace '$WORKSPACE_NAME'..."
  generate_env_file "VAR_" "$SCRIPT_DIR/.variables.$WORKSPACE_NAME.env"
}

copy_initialization_files() {

log INFO "[*] Copying initialization script to remote server $REMOTE_IP..."

log INFO "[*] Creating installation path..."
ssh -o StrictHostKeyChecking=no root@$REMOTE_IP << EOF
  mkdir -p "$RESX_INSTALL" "$RESX_INSTALL/$TEMPLATE_DIR" "$RESX_INSTALL/$WORKSPACE_DIR"
  chmod 777 "$RESX_INSTALL" "$RESX_INSTALL/$TEMPLATE_DIR" "$RESX_INSTALL/$WORKSPACE_DIR"
EOF

log INFO "[*] Copying initialization scripts and config files to remote server..."
scp -o StrictHostKeyChecking=no \
  ./deploy/*.* \
  ./deploy/scripts/*
  root@"$REMOTE_IP":"$RESX_INSTALL"/ || {
    log ERROR "[X] Failed to transfer initialization scripts to remote server"
    exit 1
  }

log INFO "[*] Copying template files files to remote server..."
scp -ro StrictHostKeyChecking=no \
  ./$TEMPLATE_DIR/ \
  root@"$REMOTE_IP":"$RESX_INSTALL/$TEMPLATE_DIR"/ || {
    log ERROR "[X] Failed to transfer template files to remote server"
    exit 1
  }

log INFO "[*] Copying workspaces files files to remote server..."
scp -ro StrictHostKeyChecking=no \
  ./$WORKSPACE_DIR/ \
  root@"$REMOTE_IP":"$RESX_INSTALL/$WORKSPACE_DIR"/ || {
    log ERROR "[X] Failed to transfer workspaces files to remote server"
    exit 1
  }

log INFO "[*] Debugging installation path of remote server..."
ssh -o StrictHostKeyChecking=no root@$REMOTE_IP << EOF
  ls -Rla "$RESX_INSTALL"
EOF
}

# Function to execute the initialization script on the remote server
# This function connects to the remote server via SSH,
# sources the environment variables, and runs the initialization script.
# It also ensures that the script is executable and handles any errors during execution.
execute_initialization() {
log INFO "[*] Executing REMOTE server initialization..."

if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
  chmod +x "$RESX_INSTALL/initialize-server.sh"
  "$RESX_INSTALL/initialize-server.sh" "$WORKSPACE_NAME" "$WORKSPACE_FILE" "$TERRAFORM_FILE"
EOF
then
  log ERROR "[X] Remote initialization failed on $REMOTE_IP"
  exit 1
fi
log INFO "[*] Executing REMOTE server initialization...DONE"
}

# Main function to initialize the swarm cluster
main() {
  log INFO "[*] Initializing swarm cluster $REMOTE_IP..."

  if ! create_environment_files; then
    log ERROR "[X] Failed to create environment file."
    exit 1
  fi

  if ! copy_initialization_files; then
    log ERROR "[X] Failed to copy configuration files to remote server."
    exit 1
  fi

  if ! execute_initialization; then
    log ERROR "[X] Remote initialization failed."
    exit 1
  fi

  log INFO "[+] Initializing swarm cluster $REMOTE_IP... DONE"
}

main

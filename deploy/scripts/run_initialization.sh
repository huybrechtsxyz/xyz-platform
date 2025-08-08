#!/bin/bash
#===============================================================================
# Script Name   : run_initialization.sh
# Description   : 
# Usage         : ./run_initialization.sh <MATRIX - NAME>
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-08
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
VARIABLE_FILE="$SCRIPT_DIR/vars.$WORKSPACE_NAME.ws.env"
SECRET_FILE="$SCRIPT_DIR/secrets.$WORKSPACE_NAME.ws.env"

# Load script utilities
source "$SCRIPT_DIR/utilities.sh"
load_script "$SCRIPT_DIR/use_workspace.sh"
load_script "$SCRIPT_DIR/use_terraform.sh"

# Load terraform data
TF_DATA=$(get_tf_data "$TERRAFORM_FILE")
VM_DATA=$(get_tf_server_by_name "$TF_DATA" "$SERVER_NAME")
REMOTE_IP=$(get_tf_vm_publicip "$VM_DATA")
RESX_NAME=$(get_tf_vm_resource "$VM_DATA")

# Load workspace data
WS_DATA=$(get_ws_data "$WORKSPACE_NAME" "$WORKSPACE_FILE")
RESX_DATA=$(get_ws_resx_from_name "$RESX_NAME" "$WS_DATA")
RESX_INSTALL=$(get_ws_resx_installpoint "$RESX_DATA")

# Create a temporary directory for the initialization scripts
create_environment_files() {
  log INFO "[*] Creating environment files for workspace '$WORKSPACE_NAME'..."

  log INFO "[*] ...Exporting fixed environment variables for Terraform"
  export_variables "$WORKSPACE_FILE" ".spec.variables" "VAR_" "" ""
  generate_env_file "VAR_" "$VARIABLE_FILE" 

  log INFO "[*] ...Exporting secrets from $WORKSPACE_FILE"
  export_secrets "$WORKSPACE_FILE" ".spec.secrets" "SECRET_" "" ""
  generate_env_file "SECRET_" "$SECRET_FILE"

  log INFO "[*] Creating environment files for workspace '$WORKSPACE_NAME'...DONE"
}

copy_initialization_files() {

log INFO "[*] Copying initialization script to remote server $REMOTE_IP..."

log INFO "[*] Creating installation path...$RESX_INSTALL"
ssh -o StrictHostKeyChecking=no root@$REMOTE_IP << EOF
  echo "[*] Creating installation paths..."
  mkdir -p "$RESX_INSTALL" "$RESX_INSTALL/$TEMPLATE_DIR" "$RESX_INSTALL/$WORKSPACE_DIR"
  chmod 777 "$RESX_INSTALL" "$RESX_INSTALL/$TEMPLATE_DIR" "$RESX_INSTALL/$WORKSPACE_DIR"
  echo "[*] Listing installation paths..."
  ls -lRa "$RESX_INSTALL"
EOF

log INFO "[*] Copying initialization scripts and config files to remote server..."
scp -o StrictHostKeyChecking=no \
  ./deploy/scripts/* \
  root@"$REMOTE_IP":"$RESX_INSTALL"/ || {
    log ERROR "[X] Failed to transfer initialization scripts to remote server"
    exit 1
  }

log INFO "[*] Copying template files files to remote server..."
scp -r -o StrictHostKeyChecking=no \
  ./$TEMPLATE_DIR/ \
  root@"$REMOTE_IP":"$RESX_INSTALL/$TEMPLATE_DIR"/ || {
    log ERROR "[X] Failed to transfer template files to remote server"
    exit 1
  }

log INFO "[*] Copying workspaces files files to remote server..."
scp -r -o StrictHostKeyChecking=no \
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

  #if ! execute_initialization; then
  #  log ERROR "[X] Remote initialization failed."
  #  exit 1
  #fi

  log INFO "[+] Initializing swarm cluster $REMOTE_IP... DONE"
}

main

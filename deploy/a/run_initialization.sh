#!/bin/bash
#===============================================================================
# Script Name   : run_initialization.sh
# Description   : 
# Usage         : ./run_initialization.sh <WORKSPACE_NAME> <WORKSPACE_FILE> <REMOTE_IP> <MANAGER_IP>
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

# Validate environment variables
VAR_REMOTE_IP=${1:?Missing private IP of the remote server.}
VAR_MANAGER_IP=${2:?Missing private IP of the manager node.}
${VAR_PATH_TEMP:?"VAR_PATH_TEMP is not set. Please set it to the temporary variable path."}

# Set defaults variables
SCRIPT_DIR="./deploy/scripts/"
VAR_TEMP_DEPLOY="$VAR_PATH_TEMP/.deploy"
VAR_TEMP_WORKSPACES="$VAR_PATH_TEMP/workspaces"
VAR_TEMP_TEMPLATES="$VAR_PATH_TEMP/templates"

# Load script utilities
load_script "$SCRIPT_DIR/utilities.sh"
load_script "$SCRIPT_DIR/use_workspace.sh"

# Create a temporary directory for the initialization scripts
create_env_file() {
  generate_env_file "VAR_" "$SCRIPT_DIR/variables.env"
}

# Function to copy configuration files to the remote server
copy_initialization_before() {
log INFO "[*] Copying initialization script to remote server..."
ssh -o StrictHostKeyChecking=no root@$VAR_REMOTE_IP << EOF
  mkdir -p "$VAR_PATH_TEMP" "$VAR_TEMP_DEPLOY" "$VAR_TEMP_WORKSPACES" "$VAR_TEMP_TEMPLATES"
  chmod 777 "$VAR_PATH_TEMP" "$VAR_TEMP_DEPLOY" "$VAR_TEMP_WORKSPACES" "$VAR_TEMP_TEMPLATES"
EOF
}

copy_initialization_after() {
log INFO "[*] Debugging temporary path of remote server..."
ssh -o StrictHostKeyChecking=no root@$VAR_REMOTE_IP << EOF
  ls -Rla "$VAR_PATH_TEMP"
EOF
}

# Function to copy configuration files to the remote server
copy_initialization_files() {
  
  copy_initialization_before

  log INFO "[*] Copying initialization scripts to remote server..."
  scp -o StrictHostKeyChecking=no \
    ./deploy/scripts/*
    root@"$VAR_REMOTE_IP":"$VAR_TEMP_DEPLOY"/ || {
      log ERROR "[X] Failed to transfer initialization scripts to remote server"
      exit 1
    }

  log INFO "[*] Copying workspace definitions to remote server..."
  scp -ro StrictHostKeyChecking=no \
    ./workspaces/*
    root@"$VAR_REMOTE_IP":"$VAR_TEMP_WORKSPACES"/ || {
      log ERROR "[X] Failed to transfer workspace definitions to remote server"
      exit 1
    }

  log INFO "[*] Copying template definitions to remote server..."
  scp -ro StrictHostKeyChecking=no \
    ./templates/*
    root@"$VAR_REMOTE_IP":"$VAR_TEMP_TEMPLATES"/ || {
      log ERROR "[X] Failed to transfer workspace definitions to remote server"
      exit 1
    }

  copy_initialization_after
  log INFO "[*] Copying initialization files to remote server... DONE"
}

# Function to execute the initialization script on the remote server
# This function connects to the remote server via SSH,
# sources the environment variables, and runs the initialization script.
# It also ensures that the script is executable and handles any errors during execution.
execute_initialization() {
log INFO "[*] Executing REMOTE server initialization..."

if ! ssh -o StrictHostKeyChecking=no root@"$VAR_REMOTE_IP" << EOF
  chmod +x "$VAR_TEMP_DEPLOY/initialize-server.sh"
  "$VAR_TEMP_DEPLOY/initialize-server.sh" "$VAR_TEMP_DEPLOY"
EOF
then
  log ERROR "[X] Remote initialization failed on $VAR_REMOTE_IP"
  exit 1
fi
log INFO "[*] Executing REMOTE server initialization...DONE"
}

# Main function to initialize the swarm cluster
main() {
  log INFO "[*] Initializing swarm cluster $REMOTE_IP..."

  if ! create_env_file; then
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

























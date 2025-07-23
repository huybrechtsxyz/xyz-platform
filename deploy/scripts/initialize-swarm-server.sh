#!/bin/bash
set -euo pipefail

# Load utility functions and environment variables
source "$(dirname "${BASH_SOURCE[0]}")/../../deploy/scripts/utilities.sh"

# Check if the required arguments are provided
if [ "$#" -ne 3 ]; then
  log ERROR "Usage: $0 <VAR_REMOTE_IP> <VAR_PRIVATE_IP> <VAR_MANAGER_IP>"
  exit 1
fi

# Assign command line arguments to variables
: "${VAR_PATH_TEMP:="/tmp/app"}"

# Validate the input parameters
VAR_REMOTE_IP="$1"
VAR_PRIVATE_IP="$2"
VAR_MANAGER_IP="$3"

if [[ -z "$VAR_REMOTE_IP" || -z "$VAR_PRIVATE_IP" || -z "$VAR_MANAGER_IP" ]]; then
  log ERROR "One or more required arguments are empty. Please provide valid IP addresses."
  exit 1
fi

# Have a deployment path for this app
PATH_DEPLOY="$VAR_PATH_TEMP"

# Create a temporary directory for the initialization scripts
# Output files
# |- ./deploy/workspaces/variables.env
create_env_file() {
  generate_env_file "VAR_" "./deploy/variables.env"
}


# Function to copy configuration files to the remote server
# This function creates a temporary directory on the remote server,
# sets the appropriate permissions, and then copies the initialization scripts
# and cluster configuration files to that directory.
copy_initialization_files() {
log INFO "[*] Copying initialization script to remote server..."
ssh -o StrictHostKeyChecking=no root@$VAR_REMOTE_IP << EOF
  mkdir -p "$PATH_DEPLOY"
  chmod 777 "$PATH_DEPLOY"
EOF

log INFO "[*] Copying initialization scripts and cluster config to remote server..."
scp -o StrictHostKeyChecking=no \
  ./deploy/*.* \
  ./deploy/scripts/* \
  ./deploy/workspaces/* \
  root@"$VAR_REMOTE_IP":"$PATH_DEPLOY"/ || {
    echo "[x] Failed to transfer initialization scripts to remote server"
    exit 1
  }

log INFO "[*] Debugging temporary path of remote server..."
ssh -o StrictHostKeyChecking=no root@$VAR_REMOTE_IP << EOF
  ls -la "$PATH_DEPLOY"
EOF
}

# Function to execute the initialization script on the remote server
# This function connects to the remote server via SSH,
# sources the environment variables, and runs the initialization script.
# It also ensures that the script is executable and handles any errors during execution.
# Available directories and files in $VAR_PATH_TEMP
# |- ./deploy/scripts/variables.env
# |- ./deploy/scripts/*
# |- ./deploy/workspaces/*
execute_initialization() {
log INFO "[*] Executing REMOTE server initialization..."
if ! ssh -o StrictHostKeyChecking=no root@"$VAR_REMOTE_IP" << EOF
  chmod +x "$PATH_DEPLOY/initialize-remote-server.sh"
  "$PATH_DEPLOY/initialize-remote-server.sh" "$PATH_DEPLOY"
EOF
then
  log ERROR "[!] Remote initialization failed on $VAR_REMOTE_IP"
  exit 1
fi
log INFO "[*] Executing REMOTE server initialization...DONE"
}

# Main function to initialize the swarm cluster
main() {
  log INFO "[*] Initializing swarm cluster ..."

  if ! create_env_file; then
    log ERROR "[x] Failed to create environment file."
    exit 1
  fi

  if ! copy_initialization_files; then
    log ERROR "[x] Failed to copy configuration files to remote server."
    exit 1
  fi

  if ! execute_initialization; then
    log ERROR "[x] Remote initialization failed."
    exit 1
  fi

  log INFO "[+] Initializing swarm cluster ...DONE"
}

main


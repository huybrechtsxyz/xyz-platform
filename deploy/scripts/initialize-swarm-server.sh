#!/bin/bash
set -euo pipefail

# Validate that the APP_PATH_TEMP variable is set and is a valid directory.
: "${APP_PATH_TEMP:="/tmp/app"}"
if [[ ! -d "$APP_PATH_TEMP" ]]; then
  echo "Temporary path $APP_PATH_TEMP does not exist. Please create it or set a different path."
  exit 1
fi

# Load utility functions and environment variables
source "$(dirname "${BASH_SOURCE[0]}")/../../deploy/scripts/utilities.sh"

# Check if the required arguments are provided
if [ "$#" -ne 3 ]; then
  log ERROR "Usage: $0 <APP_REMOTE_IP> <APP_PRIVATE_IP> <APP_MANAGER_IP>"
  exit 1
fi

# Assign command line arguments to variables
APP_REMOTE_IP="$1"
APP_PRIVATE_IP="$2"
APP_MANAGER_IP="$3"
if [[ -z "$APP_REMOTE_IP" || -z "$APP_PRIVATE_IP" || -z "$APP_MANAGER_IP" ]]; then
  log ERROR "One or more required arguments are empty. Please provide valid IP addresses."
  exit 1
fi

# Create a temporary directory for the initialization scripts
create_env_file() {
  generate_env_file "APP_" "./deploy/scripts/variables.env"
}

# Function to copy configuration files to the remote server
# This function creates a temporary directory on the remote server,
# sets the appropriate permissions, and then copies the initialization scripts
# and cluster configuration files to that directory.
copy_config_files() {
log INFO "[*] Copying initialization script to remote server..."
ssh -o StrictHostKeyChecking=no root@$APP_REMOTE_IP << EOF
  mkdir -p "$APP_PATH_TEMP"
  chmod 777 "$APP_PATH_TEMP"
EOF

log INFO "[*] Copying initialization scripts and cluster config to remote server..."
scp -o StrictHostKeyChecking=no \
  ./deploy/scripts/*.* \
  ./deploy/workspaces/*.* \
  root@"$APP_REMOTE_IP":"$APP_PATH_TEMP"/ || {
    echo "[x] Failed to transfer initialization scripts to remote server"
    exit 1
  }
}

# Function to execute the initialization script on the remote server
# This function connects to the remote server via SSH,
# sources the environment variables, and runs the initialization script.
# It also ensures that the script is executable and handles any errors during execution.
execute_initialization() {
log INFO "[*] Executing REMOTE server initialization..."
if ! ssh -o StrictHostKeyChecking=no root@"$APP_REMOTE_IP" << EOF
  set -e
  echo "[*] Executing initialization on REMOTE server..."
  set -a
  source "$APP_PATH_TEMP/variables.env"
  source "$APP_PATH_TEMP/utilities.sh"
  set +a
  chmod +x "$APP_PATH_TEMP/initialize-remote-server.sh"
  "$APP_PATH_TEMP/initialize-remote-server.sh"
  echo "[*] Initialization script executed successfully on REMOTE server."
  echo "[*] Cleaning up swarm cluster..."
  rm -rf "$APP_PATH_TEMP/*"
  echo "[*] Executing on REMOTE server...DONE"
EOF
then
  log ERROR "[!] Remote initialization failed on $APP_REMOTE_IP"
  exit 1
fi
}

# Main function to initialize the swarm cluster
main() {
  log INFO "[*] Initializing swarm cluster ..."

  if ! create_env_file; then
    log ERROR "[x] Failed to create environment file."
    exit 1
  fi

  if ! copy_config_files; then
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
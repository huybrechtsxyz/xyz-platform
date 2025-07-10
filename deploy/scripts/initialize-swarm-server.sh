#!/bin/bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../../deploy/scripts/utilities.sh"

if [ "$#" -ne 3 ]; then
  log ERROR "Usage: $0 <APP_REMOTE_IP> <APP_PRIVATE_IP> <APP_MANAGER_IP>"
  exit 1
fi

APP_REMOTE_IP="$1"
APP_PRIVATE_IP="$2"
APP_MANAGER_IP="$3"

# Create a temporary directory for the initialization scripts
create_env_file() {
  generate_env_file "APP_" "./deploy/scripts/initialize.env"
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
  source "$APP_PATH_TEMP/initialize.env"
  source "$APP_PATH_TEMP/utilities.sh"
  set +a
  chmod +x "$APP_PATH_TEMP/initialize-remote-server.sh"
  "$APP_PATH_TEMP/initialize-remote-server.sh"
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
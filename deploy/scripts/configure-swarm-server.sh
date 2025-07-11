#!/bin/bash
set -euo pipefail

# Get the remote IP and matrix from the arguments
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <REMOTE_IP> <MATRIX>"
  exit 1
fi
REMOTE_IP="$1"
MATRIX="$2"

# Source the utilities script for logging and environment variable handling
source "$(dirname "${BASH_SOURCE[0]}")/../../deploy/scripts/utilities.sh"

# Create secret and variable files based on expected prefixes
# Creates variable.env and secrets.env files
# Creates terraform.json file with the provided MATRIX
create_secret_file() {
  generate_env_file "VAR_" "./src/variables.env"
  generate_env_file "SECRET_" "./src/secrets.env"
  echo "$MATRIX" > "./src/terraform.json"
}

# Initializes the remote server by creating necessary directories
# Creates the temporary application path and subdirectories on the remote server
# This is done to ensure the remote server has the necessary structure before copying files
init_copy_files() {
log INFO "[*] Initializing REMOTE configuration..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
mkdir -p "$APP_PATH_TEMP" "$APP_PATH_TEMP"/deploy "$APP_PATH_TEMP"/src
echo "[*] Initializing REMOTE server...DONE"
EOF
then
log ERROR "[!] Initializing configuration failed on $REMOTE_IP"
exit 1
fi
}

copy_config_files() {
  log INFO "[*] Copying environment files to remote server..."
  shopt -s nullglob
  log INFO "[*] Copying environment files to remote server...Deploy"
  scp -o StrictHostKeyChecking=no \
    ./deploy/scripts/*.* \
    root@"$REMOTE_IP":"$APP_PATH_TEMP"/deploy || {
      log ERROR "[x] Failed to transfer configuration scripts to remote server"
      exit 1
    }
  log INFO "[*] Copying environment files to remote server...Sources"
  scp -o StrictHostKeyChecking=no \
    ./deploy/workspaces/*.* \
    ./scripts/*.sh \
    ./src/*.* \
    root@"$REMOTE_IP":"$APP_PATH_TEMP"/src || {
      log ERROR "[x] Failed to transfer configuration scripts to remote server"
      exit 1
    }
  log INFO "[+] Copying environment files to remote server...DONE"
}

# Configures the remote server by executing the configuration script
# This script is executed on the remote server to set up the environment
# It sources the necessary environment files and runs the configuration script
# The script is executed in a non-interactive SSH session
configure_server() {
log INFO "[*] Executing REMOTE configuration..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
set -e
echo "[*] Executing on REMOTE server..."
echo "[*] Using temporary path: $APP_PATH_TEMP"
shopt -s nullglob
echo "[*] Executing on REMOTE server...Copy source files"
set -a
source "$APP_PATH_TEMP/src/variables.env"
source "$APP_PATH_TEMP/src/secrets.env"
source "$APP_PATH_TEMP/src/utilities.sh"
set +a
chmod +x "$APP_PATH_TEMP/deploy/configure-remote-server.sh"
"$APP_PATH_TEMP/deploy/configure-remote-server.sh"
echo "[*] Executing on REMOTE server...DONE"
EOF
then
log ERROR "[!] Remote configuration failed on $REMOTE_IP"
exit 1
fi
}

main() {
  log INFO "[*] Configuring remote server at $REMOTE_IP..."

  if ! create_secret_file; then
    log ERROR "[x] Failed to create environment file."
    exit 1
  fi

  if ! init_copy_files; then
    log ERROR "[x] Failed to initialize remote configuration"
    exit 1
  fi

  if ! copy_config_files; then
    log ERROR "[x] Failed to copy configuration files to remote server"
    exit 1
  fi

  if ! configure_server; then
    log ERROR "[x] Failed to configure remote server"
    exit 1
  fi

  log INFO "[+] Configuring remote server at $REMOTE_IP...DONE"
}

main

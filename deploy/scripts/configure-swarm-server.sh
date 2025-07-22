#!/bin/bash
set -euo pipefail

# Get the remote IP and matrix from the arguments
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <REMOTE_IP> <MATRIX>"
  exit 1
fi

REMOTE_IP="$1"
MATRIX="$2"

if [[ ! "$REMOTE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Invalid IP address format: $REMOTE_IP"
  exit 1
fi
if [[ -z "$MATRIX" ]]; then
  echo "MATRIX cannot be empty"
  exit 1
fi

# Source the utilities script for logging and environment variable handling
source "$(dirname "${BASH_SOURCE[0]}")/../../deploy/scripts/utilities.sh"

# Create secret and variable files based on expected prefixes
# Creates variable.env 
# Creates secrets.env
# Creates terraform.json file with the provided MATRIX
create_secret_file() {
  generate_env_file "VAR_" "./deploy/variables.env"
  generate_env_file "SECRET_" "./deploy/secrets.env"
  echo "$MATRIX" > "./deploy/terraform.json"
}

# Initializes the remote server by creating necessary directories
# Creates the temporary application path and subdirectories on the remote server
# This is done to ensure the remote server has the necessary structure before copying files
init_copy_files() {
log INFO "[*] Initializing REMOTE configuration..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
mkdir -p "$VAR_PATH_TEMP" "$VAR_PATH_TEMP/.deploy" "$VAR_PATH_TEMP/.config"
echo "[*] Initializing REMOTE server...DONE"
EOF
then
log ERROR "[!] Initializing configuration failed on $REMOTE_IP"
exit 1
fi
}

# Copies configuration files to the remote server
# This function copies the necessary scripts and configuration files to the remote server
# It ensures that the remote server has the required files to run the configuration script
# The files are copied to the temporary application path created earlier
copy_config_files() {
  log INFO "[*] Copying environment files to remote server..."
  shopt -s nullglob
  log INFO "[*] Copying environment files to remote server...Deploy"
  scp -o StrictHostKeyChecking=no \
    ./deploy/scripts/*.* \
    root@"$REMOTE_IP":"$VAR_PATH_TEMP/.deploy" || {
      log ERROR "[x] Failed to transfer configuration scripts to remote server"
      exit 1
    }
  log INFO "[*] Copying environment files to remote server...Sources"
  scp -o StrictHostKeyChecking=no \
    ./deploy/workspaces/*.* \
    ./deploy/*.* \
    ./scripts/*.sh \
    root@"$REMOTE_IP":"$VAR_PATH_TEMP/.config" || {
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
echo "[*] Using temporary path: $VAR_PATH_TEMP"
shopt -s nullglob
set -a
source "$VAR_PATH_TEMP/.config/variables.env"
source "$VAR_PATH_TEMP/.config/secrets.env"
source "$VAR_PATH_TEMP/.deploy/utilities.sh"
set +a
chmod +x "$VAR_PATH_TEMP/.deploy/configure-remote-server.sh"
"$VAR_PATH_TEMP/.deploy/configure-remote-server.sh"
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

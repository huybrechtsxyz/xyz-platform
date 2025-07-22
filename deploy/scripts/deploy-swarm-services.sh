#!/bin/bash
set -euo pipefail

: "${VAR_WORKSPACE:?Missing WORKSPACE env var}"
: "${VAR_ENVIRONMENT:?Missing ENVIRONMENT env var}"
: "${VAR_SERVICEDATA:?Missing SERVICEDATA env var}"
: "${VAR_SERVERINFO:?Missing SERVERINFO env var}"
: "${VAR_TERRAFORM:?Missing TERRAFORM env var}"
: "${VAR_PATH_TEMP:?Missing PATH_TEMP env var}"

source "$(dirname "${BASH_SOURCE[0]}")/utilities.sh"

log INFO "Reading service and information"
MANAGER_IP=$(jq -r '.ip' <<< "$VAR_SERVERINFO")
MANAGER_LABEL=$(jq -r '.label' <<< "$VAR_SERVERINFO")
SERVICE_ID=$(jq -r '.service.id' <<< "$VAR_SERVICEDATA")
SERVICE_PATH="./service/$SERVICE_ID/"

if [[ -z "$MANAGER_IP" ]]; then
  echo "Error: MANAGER_IP is null or missing"
  exit 1
fi

if [[ -z "$SERVICE_ID" ]]; then
  echo "Error: SERVICE_ID is null or missing"
  exit 1
fi

# Replaces the environment.secrets.env file with real values from bitwarden
# Creates or updates the environment file with required values
# Get the VAR_ variables in a file and merge the appropriate service vars
# Get the SECRET_ variables in a afile and merge the created secret file
create_environment_files() {
  log INFO "Fetching secrets for service: $SERVICE_ID ($VAR_ENVIRONMENT)..."
  INPUT_FILE="$SERVICE_PATH/config/$VAR_ENVIRONMENT.secrets.env"
  OUTPUT_FILE="$SERVICE_PATH/config/$VAR_ENVIRONMENT.secrets.tmp"
  log INFO "Secrets definition file: $INPUT_FILE"
  touch "$OUTPUT_FILE"

  if [[ ! -f "$INPUT_FILE" ]]; then
    echo "No secrets file found at $INPUT_FILE"
    return 0
  fi

  while IFS='=' read -r VAR UUID; do
    # Skip empty lines or comments
    [[ -z "$VAR" || -z "$UUID" || "$VAR" =~ ^# ]] && continue
    VAR=$(echo "$VAR" | xargs)   # Trim whitespace
    UUID=$(echo "$UUID" | xargs)

    VALUE=$(bw get password "$UUID" 2>/dev/null)
    if [[ $? -ne 0 ]]; then
      log ERROR "Failed to retrieve secret for $VAR (UUID: $UUID)"
      continue
    fi

    echo "$VAR=$VALUE" >> "$OUTPUT_FILE"
  done < "$INPUT_FILE"
  log INFO "Fetching secrets for service: $SERVICE_ID ($VAR_ENVIRONMENT) ...DONE"

  log INFO "Fetching variables for service ..."
  generate_env_file "VAR_" "$SERVICE_PATH/config/variables.env"
  generate_env_file "SECRET_" "$SERVICE_PATH/config/secrets.env"

  log INFO "Merging variables for service ..."
  merge_env_file "$SERVICE_PATH/config/variables.env" "$SERVICE_PATH/config/$VAR_ENVIRONMENT.secrets.env" "$SERVICE_PATH/config/variables.env"
  merge_env_file "$SERVICE_PATH/config/secrets.env" "$SERVICE_PATH/config/$VAR_ENVIRONMENT.secrets.tmp" "$SERVICE_PATH/config/secrets.env"

  log INFO "Removing temporary secrets ..."
  rm "$SERVICE_PATH/config/$VAR_ENVIRONMENT.secrets.tmp"
  log INFO "Fetching variables for service ...DONE"
}

# Initializes the remote server by creating necessary directories
# Creates the temporary application path and subdirectories on the remote server
# This is done to ensure the remote server has the necessary structure before copying files
init_copy_files() {
log INFO "[*] Initializing REMOTE configuration..."
if ! ssh -o StrictHostKeyChecking=no root@"$REMOTE_IP" << EOF
mkdir -p "$VAR_PATH_TEMP" "$VAR_PATH_TEMP/.deploy" "$VAR_PATH_TEMP/$SERVICE_ID"
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
copy_service_files() {
  log INFO "[*] Copying service files to remote server..."
  shopt -s nullglob
  log INFO "[*] Copying service files to remote server...Deploy"
  scp -o StrictHostKeyChecking=no \
    ./deploy/scripts/* \
    root@"$MANAGER_IP":"$VAR_PATH_TEMP/.deploy/" || {
      log ERROR "[x] Failed to transfer configuration scripts to remote server"
      exit 1
    }
  log INFO "[*] Copying service files to remote server...Service"
  shopt -s nullglob
  scp -o StrictHostKeyChecking=no \
    $SERVICE_PATH/config/*.* \
    $SERVICE_PATH/scripts/*.sh \
    root@"$MANAGER_IP":"$VAR_PATH_TEMP/$SERVICE_ID/" || {
      log ERROR "[x] Failed to transfer configuration scripts to remote server"
      exit 1
    }
  log INFO "[+] Copying environment files to remote server...DONE"
}

# Configures the remote service by executing the configuration script
# This script is executed on the remote server to set up the service
# It sources the necessary environment files and runs the configuration script
# The script is executed in a non-interactive SSH session
configure_service() {
log INFO "[*] Executing REMOTE deployment..."
if ! ssh -o StrictHostKeyChecking=no root@"$MANAGER_IP" << EOF
set -e
echo "[*] Executing on REMOTE server..."
echo "[*] Using temporary path: $VAR_PATH_TEMP"
shopt -s nullglob
set -a
source "$VAR_PATH_TEMP/$SERVICE_ID/variables.env"
source "$VAR_PATH_TEMP/$SERVICE_ID/secrets.env"
source "$VAR_PATH_TEMP/.deploy/utilities.env"
set +a
chmod +x "$VAR_PATH_TEMP/.deploy/deploy-remote-services.sh"
"$VAR_PATH_TEMP/.deploy/deploy-remote-services.sh" "$SERVICE_ID" "$MANAGER_LABEL" 
echo "[*] Executing on REMOTE server...DONE"
EOF
then
log ERROR "[!] Remote deployment failed on $MANAGER_IP"
exit 1
fi
}

main() {
  log INFO "[*] Deploying service $SERVICE_ID on $MANAGER_IP ..."

  # Save the service data and server info for the service
  echo "$VAR_SERVICEDATA" > "$SERVICE_PATH/config/registry.json"

  # Create variable file for the service
  if ! create_environment_files; then
    log ERROR "[x] Failed to create environment file."
    exit 1
  fi

  # Initialize file copy
  if ! init_copy_files; then
    log ERROR "[x] Failed to initialize file copy."
    exit 1
  fi

  # Copy service files
  if ! copy_service_files; then
    log ERROR "[x] Failed to copy service files."
    exit 1
  fi

  # Configure service on remote machine
  if ! configure_service; then
    log ERROR "[x] Failed to configure remote service."
    exit 1
  fi

  # Cleanup
  rm -rf "$VAR_PATH_TEMP/$SERVICE_ID"/*

  log INFO "[*] Deploying servicse $SERVICE_ID on $MANAGER_IP ... DONE"
}

main

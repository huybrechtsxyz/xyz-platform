#!/bin/bash
set -eo pipefail
HOSTNAME=$(hostname)
SERVICE_ID="$1"
MANAGER_ID="$2"

if [[ -z "$SERVICE_ID" ]]; then
  echo "Error: SERVICE_ID is null or missing"
  exit 1
fi

if [[ -z "$MANAGER_ID" ]]; then
  echo "Error: MANAGER_ID is null or missing"
  exit 1
fi

# All files are either in $PATH_TEMP/.config or $PATH_TEMP/.deploy!
log INFO "[*] Getting workspace and terraform files"
WORKSPACE_FILE=$(get_workspace_file "$PATH_TEMP/.config" "$WORKSPACE") || exit 1
log INFO "[*] Getting workspace and terraform files $WORKSPACE_FILE"
TERRAFORM_FILE=$(get_terraform_file "$PATH_TEMP/.config") || exit 1
log INFO "[*] Getting workspace and terraform files $TERRAFORM_FILE"

create_service() {
  log INFO "[*] Starting service setup: $WORKSPACE on host $hostname"

  log INFO "[+] Starting service setup: $WORKSPACE on host $hostname"
}

main() {
  log INFO "[*] Configuring Service $SERVICE_ID on $HOSTNAME..."

  # Load docker secrets for service
  load_docker_secrets "$PATH_TEMP/$SERVICE_ID/secrets.env" || {
    log ERROR "[X] Error loading docker secrets for $SERVICE_ID"
    exit 1
  }

  # Create the service workspace
  create_service || {
    log ERROR "[X] Error creating workspace for $SERVICE_ID"
    exit 1
  }

  log INFO "[*] Configuring Service $SERVICE_ID on $HOSTNAME...DONE"
}

main

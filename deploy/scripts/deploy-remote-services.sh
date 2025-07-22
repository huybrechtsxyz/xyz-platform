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

SERVICE_FILE=$(get_service_file "$PATH_TEMP/.config" "$SERVICE_ID") || exit 1
log INFO "[*] Getting service definition files $SERVICE_FILE"

REGISTRY_FILE=$(get_registry_file "$PATH_TEMP/.config" "$SERVICE_ID") || exit 1
log INFO "[*] Getting service registry files $REGISTRY_FILE"

# Function that deploys the service
create_service() {
  log INFO "[*] Starting service setup: $WORKSPACE on host $hostname"

  # Get the servers from the terraform output
  mapfile -t servers < <(jq -c '.include[]' "$TERRAFORM_FILE")
  server_count=${#servers[@]}
  log INFO "[*] Terraform data loaded: $server_count servers found"

  # Get the paths from the workspace
  mapfile -t paths < <(jq -c '.service.paths[]' "$TERRAFORM_FILE")
  path_count=${#paths[@]}
  log INFO "[*] Workspace data loaded: $paths_count paths found"

  # For each server (private-ip)
  for serverinfo in "${servers[@]}"; do
    # Get server information
    local server_id=$(echo "$serverinfo" | jq -r '.label')
    local private_ip=$(echo "$serverinfo" | jq -r '.private_ip')
    
    # For each path of the service
    for pathinfo in "${paths[@]}"; do
      # Get path info
      local path_type=$(echo "$pathinfo" | jq -r '.type')
      local path_name=$(echo "$pathinfo" | jq -r '.path')
      local path_chmod=$(echo "$pathinfo" | jq -r '.chmod')
      local path_alias=""
      [[ -z "$path_name" || "$path_name" == "." ]] \
        && path_alias="$path_type" \
        || path_alias="$path_name"

      # Build the path based on {PATH_LABEL_TYPE}
      local basepathname="PATH_${server_id}_${PATHTYPE}"
      local basepath="${!basepathname}"

      # Compute full config path
      local fullpath="$basepath/$SERVICE_ID/$path_alias"

      # Add to environment variables file
      echo "${SERVICE_ID^^}_PATH_${path_alias^^}=$fullpath" >> "$SERVICE_PATH/config/$SERVICE_ID.env"

      # Add to path creation commands
      # TODO

    done

    # Make certain the paths exists on the server
    # TODO

    log INFO "[*] Copy the configuration files from temp to service..."
    # TODO

    log INFO "[*] Running configuration scripts for services..."
    # TODO

    log INFO "[*] For each template file apply the variables..."
    # TODO

  done

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

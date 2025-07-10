#!/bin/bash
set -eo pipefail
hostname=$(hostname)

createnodelabels() {
  # Get the current hostname
  log INFO "[*] Applying role label to all nodes..."
  local hostname srvrole workspace_file nodes
  hostname=$(hostname)

  # Get current hostname and parse role (3rd part of hyphen-separated hostname)
  hostname=$(hostname)
  srvrole=$(echo "$hostname" | cut -d'-' -f3)
  log INFO "[*] ... Detected role: $srvrole"

  # Workspace JSON file path (environment variables assumed set)
  workspace_file="$PATH_TEMP/src/$WORKSPACE.ws.json"
  log INFO "[*] ... Using workspace file: $workspace_file"

  # Get list of all Docker Swarm node hostnames
  log INFO "[*] ... Getting node hostnames"
  mapfile -t nodes < <(docker node ls --format '{{.Hostname}}')
  log INFO "[*] ... Found ${#nodes[@]} nodes"

  for node in "${nodes[@]}"; do
    log INFO "[*] ... Applying role label to $node..."

    # Parse role, instance from node name (3rd and 4th hyphen-separated parts)
    local role instance server
    role=$(echo "$node" | cut -d'-' -f3)
    instance=$(echo "$node" | cut -d'-' -f4)
    server="${role}-${instance}"
    log INFO "[*] ...... Setting $role=true on $node"
    log INFO "[*] ...... Setting role=$role on $node"
    log INFO "[*] ...... Setting server=$server on $node"
    log INFO "[*] ...... Setting instance=$instance on $node"

    # Initialize associative arrays
    declare -A existing_labels
    declare -A desired_labels

    # Read existing labels into associative array
    log INFO "[*] ...... Reading existing labels on $node"
    while IFS='=' read -r k v; do
      [[ -z "$k" && -z "$v" ]] && continue  # Skip empty lines or pure "="
      if [[ "$k" =~ ^[a-zA-Z0-9_.-]+$ && -n "$v" ]]; then
        existing_labels["$k"]="$v"
      else
        log WARN "[!] Skipping malformed label: $k=$v"
      fi
    done < <(
      docker node inspect "$node" \
        --format '{{range $k, $v := .Spec.Labels}}{{printf "%s=%s\n" $k $v}}{{end}}' \
        | grep -E '^[^=]+=[^=]+$'  # Optional extra guard
    )

    # Define desired standard labels
    desired_labels["$role"]="true"
    desired_labels["role"]="$role"
    desired_labels["server"]="$server"
    desired_labels["instance"]="$instance"

    # Update/add standard labels if they differ or are missing
    log INFO "[*] ...... Update/add standard labels $node"
    for key in "${!desired_labels[@]}"; do
      if [[ "${existing_labels[$key]}" != "${desired_labels[$key]}" ]]; then
        log INFO "[*] ...... Setting $key=${desired_labels[$key]}"
        docker node update --label-add "$key=${desired_labels[$key]}" "$node" || echo "[!] Warning: Failed to set $key on $node"
      fi
    done

    # Add custom labels from workspace JSON (jq filters by node id)
    log INFO "[*] ...... Add custom labels from workspace on $node"
    mapfile -t ws_labels < <(jq -r --arg id "$node" '.servers[] | select(.id == $id) | .labels[]?' "$workspace_file")
    for label in "${ws_labels[@]}"; do
      # Split label key and value
      local key="${label%%=*}"
      local val="${label#*=}"

      # Add or update label if needed
      if [[ "${existing_labels[$key]}" != "$val" ]]; then
        log INFO "[*] ...... Adding custom label $label"
        docker node update --label-add "$label" "$node" || echo "[!] Warning: Failed to add $label on $node"
      fi

      # Mark as desired to avoid removal
      desired_labels["$key"]="$val"
    done

    # Remove any labels that exist but are not desired
    log INFO "[*] ...... Cleaning up obsolete labels on $node"
    for key in "${!existing_labels[@]}"; do
      if [[ -z "${desired_labels[$key]}" ]]; then
        log INFO "[*] ...... Removing $key"
        docker node update --label-rm "$key" "$node" || echo "[!] Warning: Failed to remove $key on $node"
      fi
    done

    # Clean up arrays before next iteration
    unset existing_labels
    unset desired_labels
  done

  log INFO "[+] Applying role label to all nodes...DONE"
  return 0
}


create_volume() {
  : "${WORKSPACE:?Missing WORKSPACE env var}"
  : "${PATH_TEMP:?Missing PATH_TEMP env var}"
  : "${REPLICA_COUNT:=3}"

  WORKSPACE_FILE="$PATH_TEMP/src/$WORKSPACE.ws.json"

  if [[ ! -f "$WORKSPACE_FILE" ]]; then
    echo "ERROR: Workspace file not found: $WORKSPACE_FILE" >&2
    exit 1
  fi

  echo "[*] Using workspace file: $WORKSPACE_FILE"

  # Load servers
  mapfile -t SERVERS < <(jq -r '.servers[].id' "$WORKSPACE_FILE")
  echo "[*] Servers detected: ${SERVERS[*]}"

  # For each pathsv2 type that is NOT local, we create a volume
  jq -c '.pathsv2[]' "$WORKSPACE_FILE" | while read -r path_entry; do
    TYPE=$(jq -r '.type' <<<"$path_entry")
    REL_PATH_TEMPLATE=$(jq -r '.path' <<<"$path_entry")
    VOLUME_TYPE=$(jq -r '.volume' <<<"$path_entry")

    if [[ "$VOLUME_TYPE" == "local" ]]; then
      echo "[*] Skipping local volume for type=$TYPE"
      continue
    fi

    # Compute volume name
    VOLUME_NAME="${WORKSPACE}_${TYPE}"

    echo "[*] Creating Gluster volume: $VOLUME_NAME (type=$VOLUME_TYPE)"

    # Build brick list
    BRICKS=()
    for SERVER_ID in "${SERVERS[@]}"; do
      # Extract disk number for this type
      DISK=$(jq -r --arg id "$SERVER_ID" --arg t "$TYPE" \
        '.servers[] | select(.id==$id) | .mounts[] | select(.type==$t) | .disk' \
        "$WORKSPACE_FILE")

      if [[ -z "$DISK" || "$DISK" == "null" ]]; then
        echo "[!] No disk mapping for server=$SERVER_ID type=$TYPE; skipping this server"
        continue
      fi

      # Compose mount point
      MOUNT_TEMPLATE=$(jq -r --arg id "$SERVER_ID" '.servers[] | select(.id==$id) | .mountpoint' "$WORKSPACE_FILE")
      MOUNT_PATH="${MOUNT_TEMPLATE//\$\{disk\}/$DISK}"

      # Compose brick path
      SERVICE_DIR="${REL_PATH_TEMPLATE//\$\{service\}/glusterfs}"
      BRICK_PATH="$MOUNT_PATH$SERVICE_DIR"

      # Ensure directory exists
      echo "[*] Creating brick directory: $SERVER_ID:$BRICK_PATH"
      ssh "$SERVER_ID" "sudo mkdir -p '$BRICK_PATH' && sudo chown -R root:root '$BRICK_PATH'"

      BRICKS+=("${SERVER_ID}:$BRICK_PATH")
    done

    if [[ "${#BRICKS[@]}" -lt 1 ]]; then
      echo "[!] No bricks available for $VOLUME_NAME; skipping volume creation."
      continue
    fi

    # Compose volume create command
    CREATE_CMD="gluster volume create $VOLUME_NAME"

    if [[ "$VOLUME_TYPE" == "replicated" ]]; then
      CREATE_CMD+=" replica $REPLICA_COUNT"
    fi

    CREATE_CMD+=" ${BRICKS[*]}"

    echo "[*] Running: $CREATE_CMD"
    $CREATE_CMD

    echo "[*] Starting volume: $VOLUME_NAME"
    gluster volume start "$VOLUME_NAME"
  done

  echo "[+] GlusterFS configuration complete."

}

create_workspace() {
  # Validate required environment variables
  : "${WORKSPACE:?Missing WORKSPACE}"
  : "${PATH_TEMP:?Missing PATH_TEMP}"
  local hostname=$(hostname)

  # Ensure the workspace definition file exists
  log INFO "[*] Starting workspace setup: $WORKSPACE on host $hostname"
  local workspace_file="$PATH_TEMP/src/$WORKSPACE.ws.json"
  if [[ ! -f "$workspace_file" ]]; then
    log ERROR "[!] Workspace file not found: $workspace_file"
    return 1
  fi

  # Resolve server ID based on hostname
  local server_id=$(jq -r '.servers[].id' "$workspace_file" | while read -r id; do
    [[ "$hostname" == *"$id"* ]] && echo "$id" && break
  done)
  if [[ -z "$server_id" ]]; then
    log ERROR "[!] No matching server ID found for hostname: $hostname"
    return 1
  fi

  log INFO "[*] Workspace '$WORKSPACE' setup target server ID: $server_id"

  # Resolve configuration mount path
  local config_template=$(jq -r --arg id "$server_id" '.servers[] | select(.id == $id) | .mountpoint' "$workspace_file")
  local config_disk=$(jq -r --arg id "$server_id" '.servers[] | select(.id == $id) | .mounts[] | select(.type == "config") | .disk' "$workspace_file")
  local config_path="${config_template//\$\{disk\}/$config_disk}"
  export PATH_CONFIG=$config_path
  
  log INFO "[*] ... Using configuration mount point: $config_path"
  mkdir -p "$config_path"
  : > "$config_path/services.env"

  log INFO "[*] ... Copying global configuration files..."
  if ! cp -f "$PATH_TEMP"/src/*.* "$config_path/"; then
    log ERROR "[x] Failed to copy configuration files to $config_path"
    return 1
  fi

  log INFO "[*] Deploying services..."
  find "$PATH_TEMP"/src -mindepth 2 -name service.json | while read -r svc_file; do
    
    [[ -f "$svc_file" ]] || continue
    local service_id=$(jq -r '.service.id' "$svc_file")
    local server_role=$(jq -r '.service.serverrole // empty' "$svc_file")
    if [[ -z "$server_role" ]]; then
      log WARN "[!] Skipping service '$service_id': missing 'serverrole'"
      continue
    fi

    log INFO "[*] ... Setting up service: $service_id (role: $server_role)"

    # Resolve matching server based on role
    local serverdata=$(jq -c --arg role "$server_role" '.servers[] | select(.role == $role)' "$workspace_file" | head -n 1)
    if [[ -z "$serverdata" ]]; then
      log WARN "[!] No server found for role '$server_role' (service: $service_id)"
      continue
    fi

    local server_template=$(echo "$serverdata" | jq -r '.mountpoint')
    local server_mounts=$(echo "$serverdata" | jq -c '.mounts')

    # Process service paths
    jq -c '.service.paths[]' "$svc_file" | while read -r path_obj; do
      
      local path_type=$(jq -r '.type' <<< "$path_obj")
      local path_name=$(jq -r '.path // "."' <<< "$path_obj")
      local path_chmod=$(jq -r '.chmod // empty' <<< "$path_obj")

      # Resolve config disk
      local config_disk=$(jq -r --arg id "$server_id" --arg t "$path_type" '.servers[] | select(.id == $id) | .mounts[] | select(.type == $t) | .disk' "$workspace_file")
      if [[ -z "$config_disk" ]]; then
        log WARN "[!] No disk mapping for type=$path_type in service $service_id; skipping"
        continue
      fi

      local config_mnt="${config_template//\$\{disk\}/$config_disk}"
      if [[ ! -d "$config_mnt" ]]; then
        log ERROR "[!] Mount point not found: $config_mnt (for type=$path_type in $service_id)"
        continue
      fi

      # Compute full config path
      local full_path="$config_mnt/$service_id"
      [[ -z "$path_name" || "$path_name" == "." ]] && full_path="$full_path/$path_type" || full_path="$full_path/$path_name"

      mkdir -p "$full_path"

      # Install service configuration if applicable
      if [[ "${path_type,,}" == "config" && ( -z "$path_name" || "$path_name" == "." ) ]]; then
        log INFO "[*] ... Installing service files for $service_id"
        cp -fr "$PATH_TEMP"/src/"$service_id"/* "$full_path"
        cp -f "$PATH_TEMP"/src/libutils.sh "$full_path"
      fi

      # Apply the correct security on the folder
      if [[ -n "$path_chmod" ]]; then
        chmod -R "$path_chmod" "$full_path" && \
          log INFO "[+] ... Created $full_path with permissions $path_chmod" || \
          log WARN "[!] Failed to chmod $full_path"
      else
        log INFO "[+] Created $full_path"
      fi

      # Export path environment variable
      local server_disk=$(echo "$server_mounts" | jq -r --arg type "$path_type" '.[] | select(.type == $type) | .disk')
      if [[ -z "$server_disk" ]]; then
        log WARN "[!] No disk mapping for type=$path_type in service $service_id; skipping"
        continue
      fi

      local server_mnt="${server_template//\$\{disk\}/$server_disk}"
      local target_path="$server_mnt/$service_id"
      [[ -z "$path_name" || "$path_name" == "." ]] && target_path="$target_path/$path_type" || target_path="$target_path/$path_name"

      # Normalize variable names
      local var_base=$(echo "$service_id" | tr '[:lower:]' '[:upper:]')
      local var_type=$(echo "$path_type" | tr '[:lower:]' '[:upper:]')
      local var_path=$(echo "$path_name" | tr '[:lower:]' '[:upper:]')

      [[ -n "$var_path" && "$var_path" != "$var_type" ]] \
        && var_name="${var_base}_PATH_${var_path}" \
        || var_name="${var_base}_PATH_${var_type}"

      echo "$var_name=\"$target_path\"" >> "$config_path/services.env"
      log INFO "[+] ... Exported $var_name=\"$target_path\""
    done
  done

  log INFO "[*] Running configuration scripts for services..."
  for script in $config_path/*/config/configure.sh; do
    local service=$(basename "$(dirname "$script")")
    log INFO "[*] ... Configuring service '$service'..."
    chmod +x "$script"
    if "$script" "$config_path"; then
      log INFO "[+] ... Successfully configured '$service'"
    else
      log WARN "[!] Failed to configure '$service'"
    fi
  done

  log INFO "[+] Workspace '$WORKSPACE' created successfully on host '$hostname'"
}

main() {
  log INFO "[*] Configuring Swarm Node: $hostname..."

  docker info --format '{{.Swarm.LocalNodeState}}' | grep -q "active" || {
    log ERROR "[x] Docker Swarm is not active. Run 'docker swarm init' first."
    exit 1
  }

  # Create docker networks and secrets only leader node
  if [[ "$hostname" == *"manager-1"* ]]; then
    createnetwork "wan-$WORKSPACE" || exit 1
    createnetwork "lan-$WORKSPACE" || exit 1
    createnetwork "lan-test" || exit 1
    createnetwork "lan-staging" || exit 1
    createnetwork "lan-production" || exit 1
    loaddockersecrets "$PATH_TEMP/src/secrets.env" || exit 1
    createnodelabels || exit 1
  fi
  
  create_workspace || exit 1

  log INFO "[*] Remote server cleanup..."
  chmod 755 "$PATH_CONFIG"/*
  rm -f "$PATH_CONFIG"/develop.env
  rm -f "$PATH_CONFIG"/secrets.env
  rm -rf "/tmp/app/"*

  log INFO "[+] Configuring Swarm Node: $hostname...DONE"
}

main

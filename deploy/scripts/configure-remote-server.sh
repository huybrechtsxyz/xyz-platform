#!/bin/bash
set -eo pipefail
hostname=$(hostname)

: "${WORKSPACE:?Missing WORKSPACE env var}"
: "${PATH_TEMP:?Missing PATH_TEMP env var}"

WORKSPACE_FILE="$PATH_TEMP/src/$WORKSPACE.ws.json"
if [[ ! -f "$workspace_file" ]]; then
  log ERROR "[!] Workspace file not found: $workspace_file"
  return 1
fi

# Fucntion that creates docker labels for each node based on its hostname
# The labels are:
# - role: the role of the node (e.g. manager, worker)
# - server: the server name (e.g. manager-1, worker-2)
# - instance: the instance number (e.g. 1, 2)
# - role=true: a boolean label indicating the role
# This function reads the workspace file to get the node information and applies the labels to all nodes
# It also reads the existing labels and updates them if they differ or are missing
# Finally, it removes any labels that exist but are not desired
createnodelabels() {
  # Get the current hostname
  log INFO "[*] Applying role label to all nodes..."
  local srvrole workspace_file nodes

  # Get current hostname and parse role (3rd part of hyphen-separated hostname)
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

# Function to create a GlusterFS cluster
# It reads the Terraform configuration file to get the private IPs of the nodes
# It then probes each node to add it to the cluster, and detaches any nodes that
# are no longer in the desired state
# It also waits for the peers to connect and shows the status of the cluster
# Returns 0 on success, 1 on failure
create-fs-cluster() {
  log INFO "[*] Creating GlusterFS cluster..."
  
  local terraform = "$PATH_TEMP/src/terraform.json"
  if [[ ! -f "$terraform" ]]; then
    log ERROR "[!] Terraform configuration file not found: $terraform"
    return 1
  fi

  # Determine my private IP
  MANAGER_IP=$(hostname -I | awk '{print $1}')
  log INFO "[*] MANAGER_IP IP: $MANAGER_IP"
  log INFO "[*] Cluster nodes: ${PRIVATE_IPS[*]}"

  # Get current peer IPs from Gluster
  readarray -t CURRENT_PEERS < <(
    gluster peer status | awk '/Hostname:/ {print $2}'
  )

  # Extract all private IPs
  readarray -t PRIVATE_IPS < <(
    jq -r '.include[].private_ip' "$terraform"
  )

  # Add any missing peers
  for ip in "${PRIVATE_IPS[@]}"; do
    if [[ "$ip" == "$MANAGER_IP" ]]; then
      continue
    fi
    if printf '%s\n' "${CURRENT_PEERS[@]}" | grep -q "^$ip$"; then
      log INFO "[*] Peer $ip already connected."
    else
      log INFO "[*] Probing new peer $ip..."
      gluster peer probe "$ip"
    fi
  done

  # Remove peers no longer in desired state
  for ip in "${CURRENT_PEERS[@]}"; do
    if [[ "$ip" == "$MANAGER_IP" ]]; then
      continue
    fi
    if printf '%s\n' "${PRIVATE_IPS[@]}" | grep -q "^$ip$"; then
      # Still desired
      continue
    else
      log WARN "[!] Peer $ip is no longer in configuration. Detaching..."
      gluster peer detach "$ip" force || echo "[ERROR] Failed to detach $ip"
    fi
  done

  # Wait for peers
  log INFO "[*] Waiting for peers to connect..."
  sleep 5
  gluster peer status

  log INFO "[+] GlusterFS cluster created successfully."
}

# Function to create server paths for GlusterFS volumes
# It reads the workspace file to get the paths and mount points for each server
# It then creates the directories on each server via SSH
# Returns 0 on success, 1 on failure
create-fs-paths() {
  log INFO "[*] Starting GlusterFS volume creation..."

  # Check if workspace file exists and is readable
  if [[ ! -r "$WORKSPACE_FILE" ]]; then
    log ERROR "[X] Workspace file '$WORKSPACE_FILE' not found or not readable."
    return 1
  fi

  # Extract paths into associative array PATH_MAP[type]=path
  declare -A PATH_MAP

  # Read paths with error check
  while IFS=$'\t' read -r type path; do
    if [[ -z "$type" || -z "$path" ]]; then
      log WARN "[!] Skipping invalid path entry (type or path empty)."
      continue
    fi
    PATH_MAP["$type"]="$path"
    log INFO "[*] ... Loaded path for type '$type': $path"
  done < <(jq -r '.paths[] | "\(.type)\t\(.path)"' "$WORKSPACE_FILE") || {
    log ERROR "[X] Failed to parse paths from $WORKSPACE_FILE"
    return 1
  }

  # Get servers count
  servers_count=$(jq '.servers | length' "$WORKSPACE_FILE") || {
    log ERROR "[X] Failed to parse servers count from $WORKSPACE_FILE"
    return 1
  }
  if ! [[ "$servers_count" =~ ^[0-9]+$ ]]; then
    log ERROR "[X] Invalid servers count: $servers_count"
    return 1
  fi

  log INFO "[*] Found $servers_count servers."

  for (( i=0; i<servers_count; i++ )); do
    # Get server id
    server_id=$(jq -r ".servers[$i].id" "$WORKSPACE_FILE")
    mountpoint_template=$(jq -r ".servers[$i].mountpoint" "$WORKSPACE_FILE")

    if [[ -z "$server_id" || -z "$mountpoint_template" ]]; then
      log ERROR "[X] Missing server id or mountpoint for server index $i"
      return 1
    fi

    log INFO "[*] Processing server '$server_id' with mountpoint template '$mountpoint_template'"

    mounts_count=$(jq ".servers[$i].mounts | length" "$WORKSPACE_FILE") || {
      log ERROR "[X] Failed to get mounts count for server '$server_id'"
      return 1
    }
    if ! [[ "$mounts_count" =~ ^[0-9]+$ ]]; then
      log ERROR "[X] Invalid mounts count '$mounts_count' for server '$server_id'"
      return 1
    fi

    mkdir_cmds=()
    for (( j=0; j<mounts_count; j++ )); do
      mount_type=$(jq -r ".servers[$i].mounts[$j].type" "$WORKSPACE_FILE")
      disk=$(jq -r ".servers[$i].mounts[$j].disk" "$WORKSPACE_FILE")

      if [[ -z "$mount_type" || -z "$disk" ]]; then
        log WARN "[!] Missing mount type or disk at server $server_id mount index $j, skipping"
        continue
      fi

      base_path="${PATH_MAP[$mount_type]}"
      if [[ -z "$base_path" ]]; then
        log WARN "[!] No base path found for mount type '$mount_type' on server '$server_id'"
        continue
      fi

      resolved_mountpoint="${mountpoint_template//\$\{disk\}/$disk}"
      full_path="${resolved_mountpoint}${base_path}"

      log INFO "[*] Preparing mkdir command for path '$full_path'"

      mkdir_cmds+=("mkdir -p '$full_path'")
    done

    if [[ ${#mkdir_cmds[@]} -eq 0 ]]; then
      log WARN "[!] No directories to create for server '$server_id', skipping..."
      continue
    fi

    log INFO "Creating directories on server '$server_id':"
    for cmd in "${mkdir_cmds[@]}"; do
      echo "  $cmd"
    done

    # Test SSH connection first
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$server_id" "echo connected" &>/dev/null; then
      log ERROR "[X] Cannot connect to server '$server_id' via SSH."
      return 1
    fi

    commands=$(IFS='; '; echo "${mkdir_cmds[*]}")

    # Retry logic for ssh command up to 3 times
    local attempt=1
    local max_attempts=3
    while (( attempt <= max_attempts )); do
      if ssh "root@${server_id}" "bash -c \"$commands\""; then
        log INFO "[*] Directories created successfully on server '$server_id' (attempt $attempt)."
        break
      else
        log WARN "[!] Attempt $attempt: Failed to create directories on server '$server_id'. Retrying..."
        ((attempt++))
        sleep 2
      fi
    done

    if (( attempt > max_attempts )); then
      log ERROR "[X] Failed to create directories on server '$server_id' after $max_attempts attempts."
      return 1
    fi
  done

  log INFO "[+] GlusterFS volume '$volume_name' created successfully."
}

# Function to create GlusterFS volumes based on the workspace configuration
# It reads the workspace file to get the mount types, paths, and volume types
# It then creates the volumes with the appropriate bricks and starts them
create-fs-volumes(){
  log INFO "[*] Creating GlusterFS volumes..."

  # Derive workspace prefix from filename
  WORKSPACE_PREFIX=$WORKSPACE

  # Load mapping: mount type -> base path and volume type
  declare -A PATH_MAP
  declare -A VOLUME_TYPE_MAP
  while IFS=$'\t' read -r type path volume; do
    PATH_MAP["$type"]="$path"
    VOLUME_TYPE_MAP["$type"]="$volume"
  done < <(jq -r '.paths[] | "\(.type)\t\(.path)\t\(.volume)"' "$WORKSPACE_FILE")

  # Load mapping: label -> private_ip from terraform.json
  declare -A SERVER_IP_MAP
  while IFS=$'\t' read -r label ip; do
    SERVER_IP_MAP["$label"]="$ip"
  done < <(jq -r '.include[] | "\(.label)\t\(.private_ip)"' terraform.json)

  # Iterate over servers in workspace
  servers_count=$(jq '.servers | length' "$WORKSPACE_FILE")

  # Build a map of volume name -> bricks
  declare -A BRICKS_MAP

  for (( i=0; i<servers_count; i++ )); do
    server_id=$(jq -r ".servers[$i].id" "$WORKSPACE_FILE")
    mountpoint_template=$(jq -r ".servers[$i].mountpoint" "$WORKSPACE_FILE")
    mounts_count=$(jq ".servers[$i].mounts | length" "$WORKSPACE_FILE")

    # Normalize server_id (infra-1) -> label (infra_1)
    normalized_id="${server_id//-/_}"
    ip="${SERVER_IP_MAP[$normalized_id]}"
    if [[ -z "$ip" ]]; then
      log ERROR "No private_ip found for server '$server_id' (label '$normalized_id'), skipping."
      continue
    fi

    for (( j=0; j<mounts_count; j++ )); do
      mount_type=$(jq -r ".servers[$i].mounts[$j].type" "$WORKSPACE_FILE")
      disk=$(jq -r ".servers[$i].mounts[$j].disk" "$WORKSPACE_FILE")

      base_path="${PATH_MAP[$mount_type]}"
      volume_type="${VOLUME_TYPE_MAP[$mount_type]}"

      if [[ -z "$base_path" || -z "$volume_type" ]]; then
        log WARN "[!] Missing base path or volume type for mount type '$mount_type', skipping."
        continue
      fi

      # Skip local storage volumes
      if [[ "$volume_type" == "local" ]]; then
        continue
      fi

      resolved_mountpoint="${mountpoint_template//\$\{disk\}/$disk}"
      full_path="${resolved_mountpoint}${base_path}"

      brick="${ip}:${full_path}"

      # Use workspace prefix in the volume name
      volname="${WORKSPACE_PREFIX}_${mount_type}"

      BRICKS_MAP["$volname"]+="$brick "
    done
  done

  # Create and start volumes
  for volname in "${!BRICKS_MAP[@]}"; do
    bricks=(${BRICKS_MAP[$volname]})

    # Extract the mount_type back from the volname
    mount_type="${volname#${WORKSPACE_PREFIX}_}"
    volume_type="${VOLUME_TYPE_MAP[$mount_type]}"

    if [[ ${#bricks[@]} -eq 0 ]]; then
      log WARN "No bricks defined for volume '$volname', skipping."
      continue
    fi

    # Check if volume already exists
    if gluster volume info "$volname" &>/dev/null; then
      log INFO "Volume '$volname' already exists, skipping creation."
    else
      log INFO "Creating volume '$volname' of type '$volume_type' with bricks: ${bricks[*]}"

      if [[ "$volume_type" == "replicated" ]]; then
        replica_count=${#bricks[@]}
        gluster volume create "$volname" replica "$replica_count" "${bricks[@]}"
      elif [[ "$volume_type" == "distributed" ]]; then
        gluster volume create "$volname" "${bricks[@]}"
      else
        log WARN "Unknown volume type '$volume_type' for volume '$volname', skipping."
        continue
      fi

      gluster volume start "$volname"
      log INFO "Volume '$volname' created and started."
    fi
  done
  
  log INFO "[*] Creating GlusterFS volumes...DONE"
}

# Main function to configure the remote server based on its role
main_manager() {
  log INFO "[*] Configuring Manager Node: $hostname..."

  createnetwork "wan-$WORKSPACE"
  createnetwork "lan-$WORKSPACE"
  createnetwork "lan-test"
  createnetwork "lan-staging"
  createnetwork "lan-production"
  loaddockersecrets "$PATH_TEMP/src/secrets.env"
  createnodelabels || {
    log ERROR "[!] Failed to create node labels."
    return 1
  }

  create-fs-cluster || {
    log ERROR "[!] Failed to create GlusterFS cluster."
    return 1
  }

  create-fs-paths || {
    log ERROR "[!] Failed to create the base server paths."
    return 1
  }

  create-fs-volumes || {
    log ERROR "[!] Failed to create GlusterFS volumes."
    return 1
  }

  log INFO "[*] Configuring Manager Node: $hostname...DONE"
}

# Main function to configure the worker node
main_worker() {
  log INFO "[*] Configuring Worker Node: $hostname..."
  log INFO "[*] Configuring Worker Node: $hostname...DONE"
}

# Main function to configure the remote server
main() {
  log INFO "[*] Configuring Swarm Node: $hostname..."

  docker info --format '{{.Swarm.LocalNodeState}}' | grep -q "active" || {
    log ERROR "[!] Docker Swarm is not active. Run 'docker swarm init' first."
    exit 1
  }

  if [[ "$hostname" == *"manager-1"* ]]; then
    main_manager || {
      log ERROR "[!] Failed to configure manager node."
      exit 1
    }
  else
    main_worker || {
      log ERROR "[!] Failed to configure worker node."
      exit 1
    }
  fi

  log INFO "[*] Remote server cleanup..."
  chmod 755 "$PATH_CONFIG"/*
  rm -f "$PATH_CONFIG"/develop.env
  rm -f "$PATH_CONFIG"/secrets.env
  rm -rf "/tmp/app/"*

  log INFO "[+] Configuring Swarm Node: $hostname...DONE"
}

main

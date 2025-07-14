#!/bin/bash
set -eo pipefail
HOSTNAME=$(hostname)

: "${WORKSPACE:?Missing WORKSPACE env var}"
: "${PATH_TEMP:?Missing PATH_TEMP env var}"

# All files are either in $PATH_TEMP/src or $PATH_TEMP/deploy!
log INFO "[*] Getting workspace and terraform files"
WORKSPACE_FILE=$(get_workspace_file "$PATH_TEMP/src" "$WORKSPACE") || exit 1
log INFO "[*] Getting workspace and terraform files $WORKSPACE_FILE"
TERRAFORM_FILE=$(get_terraform_file "$PATH_TEMP/src") || exit 1
log INFO "[*] Getting workspace and terraform files $TERRAFORM_FILE"

# Determine my server id
log INFO "[*] Getting server information: server id"
SERVER_ID=$(get_server_id "$WORKSPACE_FILE" "$HOSTNAME") || exit 1
log INFO "[*] Getting server information: manager label"
MANAGER_LABEL=$(get_manager_id "$WORKSPACE_FILE") || exit 1
log INFO "[*] Getting server information: manager ip"
MANAGER_IP=$(get_terraform_data "$TERRAFORM_FILE" "$SERVER_ID" "manager_ip") || exit 1

# Load private IPs from terraform.json (as an array)
readarray -t PRIVATE_IPS < <(jq -r '.include[].private_ip' "$TERRAFORM_FILE")

log INFO "[*] CURRENT SERVER: $SERVER_ID"
log INFO "[*] MANAGER LABEL: $MANAGER_LABEL"
log INFO "[*] MANAGER NODE: $MANAGER_IP"
log INFO "[*] CLUSTER NODES: ${PRIVATE_IPS[*]}"

retun 0

# Fucntion that creates docker labels for each node based on its hostname
# The labels are:
# - role: the role of the node (e.g. manager, worker)
# - server: the server name (e.g. manager-1, worker-2)
# - instance: the instance number (e.g. 1, 2)
# - role=true: a boolean label indicating the role
# This function reads the workspace file to get the node information and applies the labels to all nodes
# It also reads the existing labels and updates them if they differ or are missing
# Finally, it removes any labels that exist but are not desired
create_docker_labels() {
  # Get the current hostname
  log INFO "[*] Applying role label to all nodes..."
  local srvrole nodes

  # Get current hostname and parse role (3rd part of hyphen-separated hostname)
  srvrole=$(echo "$HOSTNAME" | cut -d'-' -f3)
  log INFO "[*] ... Detected role: $srvrole"

  # Workspace JSON file path (environment variables assumed set)
  log INFO "[*] ... Using workspace file: $WORKSPACE_FILE"

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
    mapfile -t ws_labels < <(jq -r --arg id "$node" '.servers[] | select(.id == $id) | .labels[]?' "$WORKSPACE_FILE")
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

# Function to create the base server paths for GlusterFS volumes
# It reads the workspace file to get the paths and mount points for each server
# It then creates the directories on each server via SSH
# It then creates the GlusterFS volumes based on the paths
# It then creates environment variables for the paths > /PATH_TEMP/src/variables.env
# At the end, it starts the volumes
create-fs-volumes() {
  log INFO "[*] Creating GlusterFS volumes..."
  log INFO "[*] Workspace '$WORKSPACE' setup target server ID: $SERVER_ID"

  # Loop over all servers in the workspace file
  while read -r serverdata; do

    local server=$(echo "$serverdata" | jq -r '.id')
    local mountpoint=$(echo "$serverdata" | jq -r '.mountpoint')
    
    local private_ip=$(get_terraform_data "$TERRAFORM_FILE" "$server" "private_ip") || exit 1

    commands=()
    declare -A bricks_map
    declare -A volume_map

    log INFO "[*] Processing server '$server' mounts with mountpoint '$mountpoint'"

    while read -r mount; do

      # Extract mount type and disk from the mount object
      local mounttype=$(echo "$mount" | jq -r '.type')
      local mountdisk=$(echo "$mount" | jq -r '.disk')

      # Get the path information for this mount type
      local pathinfo=$(jq -r --arg type "$mounttype" '.paths[] | select(.type==$type)' "$WORKSPACE_FILE")
      if [[ -z "$pathinfo" ]]; then
        log ERROR "[!] No path information found for mount type '$mounttype'. Skipping."
        continue
      fi

      local volume=$(echo "$pathinfo" | jq -r '.volume')
      local mountpath=$(echo "$pathinfo" | jq -r '.path')
      if [[ -z "$volume" || -z "$mountpath" ]]; then
        log ERROR "[!] Missing volume or mount path for mount type '$mounttype'. Skipping."
        continue
      fi

      # Resolve the full path
      local fullpath="${mountpoint//\$\{disk\}/$mountdisk}${mountpath}"
      log INFO "[*] Creating directory with type $mounttype on server '$server': $fullpath"

      # Add the path to the creation command
      commands+=("$fullpath")

      # Add to environment variables file
      echo "PATH_${server^^}_${mounttype^^}=$fullpath" >> "$PATH_TEMP/src/variables.env"

      # Add to bricks array for GlusterFS volume creation
      # Use workspace prefix in the volume name
      # Replicated volumes will have multiple bricks
      # Distributed volumes will have a single brick
      # Local volumes will be skipped as they are not managed by GlusterFS
      if [[ $volume == "local" ]]; then
        log INFO "[*] Skipping local storage volume '$mounttype' for server '$server'."
      else
        log INFO "[*] Adding GlusterFS volume '$mounttype' for server '$server'."
        volume_map["$mounttype"]="$volume"
        local brick="${private_ip}:${fullpath}"
        volumename="${WORKSPACE}_${mounttype}"
        bricks_map["$volumename"]+="$brick "
        log INFO "[*] Adding brick '$brick' to volume '$volumename'"
      fi

    done < <(jq -c '.mounts[]' <<< "$serverdata")

    # Create the directories on the server via SSH
    # GlusterFS requires the directories to exist before creating volumes
    if [[ ${#commands[@]} -gt 0 ]]; then
      if [[ "$server" == "$SERVER_ID" ]]; then
        log INFO "[*] Creating directories on local server '$server'..."
        mkdir -p "${commands[@]}"
        log INFO "[*] Creating directories on local server '$server'...DONE"
      else
        log INFO "[*] Creating directories on server '$server' via SSH..."
        if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$server" mkdir -p "${commands[@]}"; then
          log ERROR "[!] Failed to create directories on server '$server'."
          return 1
        fi
        log INFO "[*] Directories created successfully on server '$server'."
      fi
    else
      log WARN "[!] No directories to create on server '$server'. Skipping."
    fi

    # Create and start volumes 
    # Iterate over the bricks_map to create volumes
    log INFO "[*] Creating GlusterFS volumes for server '$server'..."
    for volname in "${!bricks_map[@]}"; do
      bricks=(${bricks_map[$volname]})

      # Extract the mount_type back from the volname
      mount_type="${volname#${WORKSPACE}_}"
      volume_type="${volume_map[$mount_type]}"

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

  done < <(jq -c '.servers[]' "$WORKSPACE_FILE")

  log INFO "[+] GlusterFS volumes created successfully."
}

# Main function to configure the remote server based on its role
main_manager() {
  log INFO "[*] Configuring Manager Node: $HOSTNAME..."

  create_docker_network "wan-$WORKSPACE"
  create_docker_network "lan-$WORKSPACE"
  create_docker_network "lan-test"
  create_docker_network "lan-staging"
  create_docker_network "lan-production"
  load_docker_secrets "$PATH_TEMP/src/secrets.env"
  create_docker_labels || {
    log ERROR "[!] Failed to create node labels."
    return 1
  }

  create-fs-cluster || {
    log ERROR "[!] Failed to create GlusterFS cluster."
    return 1
  }

  create-fs-volumes || {
    log ERROR "[!] Failed to create GlusterFS volumes."
    return 1
  }

  log INFO "[*] Configuring Manager Node: $HOSTNAME...DONE"
}

# Main function to configure the worker node
main_worker() {
  log INFO "[*] Configuring Worker Node: $HOSTNAME..."
  log INFO "[*] Configuring Worker Node: $HOSTNAME...DONE"
}

# Main function to configure the remote server
main() {
  log INFO "[*] Configuring Swarm Node: $HOSTNAME..."

  docker info --format '{{.Swarm.LocalNodeState}}' | grep -q "active" || {
    log ERROR "[!] Docker Swarm is not active. Run 'docker swarm init' first."
    exit 1
  }

  if [[ "$HOSTNAME" == *"MANAGER_LABEL"* ]]; then
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

  log INFO "[+] Configuring Swarm Node: $HOSTNAME...DONE"
}

main

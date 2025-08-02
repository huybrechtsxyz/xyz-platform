#!/bin/bash
#===============================================================================
# Script Name   : configure-renote-server.sh
# Description   : Pipeline code to call remote configuration code
# Usage         : ./configure-remote-server.sh <VAR_PATH_TEMP>
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
# Available directories and files in $VAR_PATH_TEMP/.deploy
# |- ./deploy/scripts/*
# Available directories and files in $VAR_PATH_TEMP/.config
# |- ./deploy/workspaces/*
# |- ./deploy/configuration.env
# |- ./deploy/secrets.env
# |- ./deploy/terraform.json
# |- ./scripts/*
# The workspace file already contains the server paths
# |- servers[ { id=manager1, paths[ { type=config, path=/mnt/data1/etc/config } ] } ]
#===============================================================================
set -eo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

PATH_TEMP="$1"
: "${PATH_TEMP:?Missing PATH_TEMP}"
if [[ ! -d "$PATH_TEMP" ]]; then
  echo "Temporary path $PATH_TEMP does not exist."
  exit 1
fi

PATH_DEPLOY="$PATH_TEMP/.deploy"
if [[ ! -d "$PATH_DEPLOY" ]]; then
  echo "Temporary path $PATH_DEPLOY does not exist."
  exit 1
fi

PATH_CONFIG="$PATH_TEMP/.config"
if [[ ! -d "$PATH_CONFIG" ]]; then
  echo "Temporary path $PATH_CONFIG does not exist."
  exit 1
fi

PATH_DOCS="$PATH_TEMP/.docs"
if [[ ! -d "$PATH_DOCS" ]]; then
  echo "Temporary path $PATH_DOCS does not exist."
  exit 1
fi

# Sourcing variables and scripts
if [[ -f "$PATH_CONFIG/configuration.env" ]]; then
  source "$PATH_CONFIG/configuration.env"
else
  log ERROR "[X] Missing configuration.env at $PATH_CONFIG"
  exit 1
fi

if [[ -f "$PATH_DEPLOY/utilities.sh" ]]; then
  source "$PATH_DEPLOY/utilities.sh"
else
  log ERROR "[X] Missing utilities.sh at $PATH_DEPLOY"
  exit 1
fi

# Workspace and hostname
: "${WORKSPACE:?Missing WORKSPACE}"
HOSTNAME=$(hostname)

WORKSPACE_FILE=$(get_workspace_file "$PATH_CONFIG" "$WORKSPACE") || exit 1
log INFO "[*] Getting workspace file: $WORKSPACE_FILE"

# Validate the workspace definition
log INFO "[*] Validating workspace file: $WORKSPACE_FILE"
validate_workspace "$PATH_DEPLOY" "$WORKSPACE_FILE"

TERRAFORM_FILE=$(get_terraform_file "$PATH_CONFIG") || exit 1
log INFO "[*] Getting terraform file $TERRAFORM_FILE"

MANAGER_ID=$(get_manager_id "$WORKSPACE_FILE") || exit 1
log INFO "[*] Getting manager label: $MANAGER_ID"

SERVER_ID=$(get_server_id "$WORKSPACE_FILE" "$HOSTNAME") || exit 1
log INFO "[*] Getting workspace server id: $SERVER_ID"

MANAGER_IP=$(get_terraform_data "$TERRAFORM_FILE" "$SERVER_ID" "manager_ip") || exit 1
log INFO "[*] Getting private management IP for server: $MANAGER_IP"

# Function that creates docker labels for each node based on its hostname
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
    mapfile -t ws_labels < <(jq -r --arg id "$node" '.workspace.servers[] | select(.id == $id) | .labels[]?' "$WORKSPACE_FILE")
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
  log INFO "[*] Current peers $CURRENT_PEERS..."

  # Extract all private IPs
  log INFO "[*] Extracting private ips from $TERRAFORM_FILE..."
  readarray -t PRIVATE_IPS < <(
    jq -r '.include[].private_ip' "$TERRAFORM_FILE"
  )

  # Add any missing peers
  log INFO "[*] Add any missing peers"
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
  log INFO "[*] Remove peers no longer in desired state"
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
# It then creates environment variables for the paths > /PATH_CONFIG/workspace.env
# At the end, it starts the volumes
# ------------------------------------------------------------------------------
# NOTE: This function uses 'force' when creating GlusterFS volumes.
#
# Rationale for using 'force':
#   - Many nodes in this cluster do not have extra attached disks.
#   - GlusterFS is only used here for synchronizing config files and small operational data.
#   - Persistent workloads and stateful services store data in Redis, Consul, or Postgres.
#   - Therefore, creating volumes on the root (/) filesystem is acceptable.
#
# WARNING:
#   - If you expand GlusterFS usage to store large data, re-evaluate this decision.
#   - Monitor root filesystem capacity to avoid filling up the OS partition.
# ------------------------------------------------------------------------------
create-fs-volumes() {
  log INFO "[*] Creating GlusterFS volumes..."
  log INFO "[*] Workspace '$WORKSPACE' setup target server ID: $SERVER_ID"

  # Array declartions
  declare -A bricks_map
  declare -A volume_map

  # Read all workspace servers for debugging
  mapfile -t servers < <(jq -c '.workspace.servers[]' "$WORKSPACE_FILE")
  server_count=${#servers[@]}
  log INFO "[*] ... Workspace data loaded: $server_count servers found: $(jq -r '.servers[].id' "$WORKSPACE_FILE" | paste -sd "," -)"

  # Read terraform servers for debugging
  mapfile -t tservers < <(jq -c '.include[]' "$TERRAFORM_FILE")
  tserver_count=${#tservers[@]}
  log INFO "[*] ... Terraform data loaded: $tserver_count servers found: $(jq -r '.include[].label' "$TERRAFORM_FILE" | paste -sd ',')"

  # First pass: Create directories and collect all bricks
  # Loop over all servers in the workspace file
  #while read -r serverdata; do
  for serverdata in "${servers[@]}"; do
    local server=$(echo "$serverdata" | jq -r '.id')
    local private_ip=$(get_terraform_data "$TERRAFORM_FILE" "$server" "private_ip") || exit 1

    if [[ -z "$private_ip" ]]; then
      log ERROR "[!] No private_ip found for server '$server'. Skipping."
      continue
    fi

    commands=()

    log INFO "[*] ... Processing server '$server' paths for ip '$private_ip'"

    # Read mounts into an array
    mapfile -t paths < <(jq -c '.paths[]' <<< "$serverdata")

    #while read -r mount; do
    for path in "${paths[@]}"; do

      # Get the path information for this mount type
      local mounttype=$(echo "$path" | jq -r '.type')
      local fullpath=$(echo "$path" | jq -r '.path')

      # Find matching workspace path info to get volume type
      pathinfo=$(jq -r --arg type "$mounttype" '.workspace.paths[] | select(.type==$type)' "$WORKSPACE_FILE")
      local volume=$(echo "$pathinfo" | jq -r '.volume')

      if [[ -z "$pathinfo" || -z "$volume" || -z "$fullpath" ]]; then
        log ERROR "[X] Missing path or volume for type '$mounttype' on server '$server'. Skipping."
        continue
      fi

      # Resolve the full path (already in workspace file)
      log INFO "[*] ...... Creating directory for type $mounttype on server '$server': $fullpath"

      # Add the path to the creation command
      commands+=("$fullpath")

      # Add to environment variables file
      varserver=$(get_server_variable_name "$server" "$mounttype")
      echo "$varserver=$fullpath" >> "$PATH_CONFIG/$WORKSPACE.ws.env"

      # Add to bricks array for GlusterFS volume creation
      # Use workspace prefix in the volume name
      # Replicated volumes will have multiple bricks
      # Distributed volumes will have a single brick
      # Local volumes will be skipped as they are not managed by GlusterFS
      if [[ $volume == "local" ]]; then
        log INFO "[*] ...... Skipping local storage volume '$mounttype' for server '$server'."
      else
        log INFO "[*] ...... Adding GlusterFS volume '$mounttype' for server '$server'."
        volumename="${WORKSPACE}_${mounttype}"
        brick="${private_ip}:${fullpath}"
        volume_map["$mounttype"]="$volume"
        bricks_map["$volumename"]+="$brick "
        log INFO "[*] ...... Adding brick '$brick' to volume '$volumename'"
      fi

    done

    # Create the directories on the server via SSH
    # GlusterFS requires the directories to exist before creating volumes
    if [[ ${#commands[@]} -gt 0 ]]; then
      if [[ "$server" == "$SERVER_ID" ]]; then
        log INFO "[*] ...... Creating directories on local server '$server'..."
        mkdir -p "${commands[@]}"
        log INFO "[*] ...... Creating directories on local server '$server'...DONE"
      else
        log INFO "[*] ...... Creating directories on server '$server' via SSH..."
        if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$private_ip" mkdir -p "${commands[@]}"; then
          log ERROR "[!] Failed to create directories on server '$server'."
          return 1
        fi
        log INFO "[*] ...... Directories created successfully on server '$server'."
      fi
    else
      log WARN "[!] ... No directories to create on server '$server'. Skipping."
    fi
  done

  # Create and start volumes
  # Iterate over the bricks_map to create volumes
  log INFO "[*] ... Creating GlusterFS volumes ..."
  for volumename in "${!bricks_map[@]}"; do
    # Extract the mount_type back from the volname
    bricks=(${bricks_map[$volumename]})
    mounttype="${volumename#${WORKSPACE}_}"
    volumetype=${volume_map[$mounttype]}

    if [[ ${#bricks[@]} -eq 0 ]]; then
      log WARN "[!] ...... No bricks defined for volume '$volumename', skipping."
      continue
    fi

    validate_volume_configuration "$volumename" "$volumetype" "${bricks[@]}" || continue

    # Check if volume already exists
    if gluster volume info "$volumename" &>/dev/null; then
      log INFO "[*] ...... Volume '$volumename' already exists, skipping creation."
    else
      log INFO "[*] ...... Creating volume '$volumename' of type '$volumetype' with bricks: ${bricks[*]}"

      if [[ "$volumetype" == "replicated" ]]; then
        replica_count=${#bricks[@]}
        log INFO "[*] ... Executing: gluster volume create $volumename with $replica_count replicas..."
        gluster volume create "$volumename" replica "$replica_count" "${bricks[@]}" force
      elif [[ "$volumetype" == "distributed" ]]; then
        gluster volume create "$volumename" "${bricks[@]}" force
      else
        log WARN "[!] ...... Unknown volume type '$volumetype' for volume '$volumename', skipping."
        continue
      fi

      gluster volume start "$volumename"
      log INFO "[*] ...... Volume '$volumename' created and started."
    fi
  done

  # Automatically export all variables that follow
  set -a
  source "$PATH_CONFIG/$WORKSPACE.ws.env"
  set +a

  log INFO "[+] GlusterFS volumes created successfully."
}

# Prepare the required workspace for service deployment
create_workspace() {
  log INFO "[*] Starting workspace setup: $WORKSPACE on host $HOSTNAME"

  # Build the path based on {PATH_SERVER_TYPE}
  local configpathname=$(get_server_variable_name "$SERVER_ID" "CONFIG")
  local configpath="${!configpathname}"
  log INFO "[*] ... Copying global configuration files to: $configpath"

  # Copy the base configuration files and scripts
  # E.g. /mnt/data1/etc/app/
  if ! cp -f "$PATH_CONFIG"/* "$configpath/"; then
    log ERROR "[x] Failed to copy configuration files to $configpath"
    return 1
  fi

  # Set configuration path as executable
  chmod 755 "$configpath"/*.sh

  # Ensure the workspace definition file exists
  log INFO "[*] Workspace $WORKSPACE setup COMPLETE on host $HOSTNAME"
}

# Main function to configure the remote server based on its role
main_manager() {
  log INFO "[*] Configuring Manager Node: $HOSTNAME..."

  # Create docker networks
  create_docker_network "wan-$WORKSPACE"
  create_docker_network "lan-$WORKSPACE"

  # Create docker secrets and remove file
  load_docker_secrets "$PATH_CONFIG/secrets.env"
  safe_rm_rf "$PATH_CONFIG/secrets.env"

  # Create the required swarm labels
  create_docker_labels || {
    log ERROR "[X] Failed to create node labels."
    return 1
  }

  # Create the glusterfs cluster
  create-fs-cluster || {
    log ERROR "[X] Failed to create GlusterFS cluster."
    return 1
  }

  # Create the directories/volumes
  create-fs-volumes || {
    log ERROR "[X] Failed to create GlusterFS volumes."
    return 1
  }

  # Create the workspace
  create_workspace || {
    log ERROR "[X] Failed to create workspace."
    return 1
  }

  log INFO "[+] Configuring Manager Node: $HOSTNAME...DONE"
}

# Main function to configure the worker node
main_worker() {
  log INFO "[*] Configuring Worker Node: $HOSTNAME..."
  log INFO "[+] Configuring Worker Node: $HOSTNAME...DONE"
}

# Main function to configure the remote server
main() {
  log INFO "[*] Configuring Swarm Node: $HOSTNAME..."

  docker info --format '{{.Swarm.LocalNodeState}}' | grep -q "active" || {
    log ERROR "[!] Docker Swarm is not active. Run 'docker swarm init' first."
    exit 1
  }

  if [[ "$HOSTNAME" == *"$MANAGER_ID"* ]]; then
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
  # PATH_TEMP PATH_CONFIG PATH_DEPLOY
  safe_rm_rf /tmp/app/*

  log INFO "[+] Configuring Swarm Node: $HOSTNAME...DONE"
}

main

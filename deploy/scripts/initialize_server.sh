#!/bin/bash
#===============================================================================
# Script Name   : initialize_server.sh
# Description   : Server initialization script
# Usage         : ./initialize_server.sh
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
# Available directories and files in $PATH_DEPLOY (/tmp/app/.deploy)
# |- ./deploy/scripts/variables.env
# |- ./deploy/scripts/*
# |- ./deploy/workspaces/*
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_NAME="${1:-}"
WORKSPACE_FILE="${2:-}"
TERRAFORM_FILE="${3:-}"

HOSTNAME=$(hostname)
:${HOSTNAME:?ERROR HOSTNAME variable is not set.}
:${WORKSPACE_NAME:?ERROR WORKSPACE_NAME variable is not set.}
:${WORKSPACE_FILE:?ERROR WORKSPACE_FILE variable is not set.}
:${TERRAFORM_FILE:?ERROR TERRAFORM_FILE variable is not set.}

# Load utility functions and variables
load_script "$SCRIPT_DIR/utilities.sh"
load_script "$SCRIPT_DIR/use_workspace.sh"
load_script "$SCRIPT_DIR/use_terraform.sh"

# Load workspace variables
load_source "$SCRIPT_DIR/.variables.$ WORKSPACE_NAME.env"












v"
TERRAFORM_FILE="$SCRIPT_DIR/.terraform.$WORKSPACE_NAME.json"
WORKSPACE_FILE="$SCRIPT_DIR/.workspace.$WORKSPACE_NAME.yml"

# Load terraform file and data
TERRAFORM_DATA=$(get_terraform_server_by_hostname "$TERRAFORM_FILE" "$HOSTNAME")
if [[ -z "$TERRAFORM_DATA" ]]; then
  log ERROR "[X] No Terraform data found for hostname: $HOSTNAME"
  exit 1
fi

# Extract server details from Terraform data
RESOURCE_NAME=$(get_terraform_server_resourceid "$TERRAFORM_DATA")
MANAGER_LABEL=$(get_terraform_server_label "$TERRAFORM_DATA")
PRIVATE_IP=$(get_terraform_server_privateip "$TERRAFORM_DATA")
MANAGER_IP=$(get_terraform_server_managerip "$TERRAFORM_DATA")

# Get workspace data
WORKSPACE_DATA=$(get_workspace_data "$WORKSPACE_NAME" "$WORKSPACE_FILE")
if [[ -z "$WORKSPACE_DATA" ]]; then
  log ERROR "[X] Failed to retrieve workspace data for '$WORKSPACE_NAME' from '$WORKSPACE_FILE'."
  exit 1
fi

# Get the resource block
RESOURCE_DATA=$(get_workspace_resource_from_resourceid "$RESOURCE_NAME" "$WORKSPACE_DATA")

# Install yq if not already installed
install_yq() {
  # Check if yq is installed and matches required version
  if ! command -v yq >/dev/null; then
    log INFO "[*] yq not found. Installing..."
    YQ_VERSION=$(curl -s https://api.github.com/repos/mikefarah/yq/releases/latest | grep tag_name | cut -d '"' -f 4)
    curl -L "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" -o /usr/local/bin/yq
    chmod +x /usr/local/bin/yq
    log INFO "[*] yq installed successfully."
  else
    log INFO "[*] yq is already installed: $(yq --version)"
  fi
}

# Install the private key if it exists
install_private_key() {
  log INFO "[*] Installing uploaded private key..."
  mkdir -p ~/.ssh
  mv /root/.ssh/id_rsa_temp ~/.ssh/id_rsa
  chmod 600 ~/.ssh/id_rsa
  echo -e "Host *\n  StrictHostKeyChecking no\n" > ~/.ssh/config
  log INFO "[+] Installing uploaded private key...DONE"
}

# Function to install Docker if not already installed
install_docker() {
  if ! command -v docker &> /dev/null; then
    log INFO "[*] Installing Docker..."
    apt-get update
    apt-get install -y ca-certificates curl gnupg lsb-release
    curl -fsSL https://get.docker.com | bash
    log INFO "[+] Installing Docker...DONE"
  else
    log INFO "[*] Docker is already installed."
  fi
}

# Function to install GlusterFS if not already installed
install_gluster() {
  log INFO "[*] Installing GlusterFS..."
  if ! command -v glusterfs --version &> /dev/null; then
    apt-get install -y glusterfs-server
    systemctl enable --now glusterd
    log INFO "[+] Installing GlusterFS...DONE"
  else
    log INFO "[*] GlusterFS is already installed."
  fi
}

# Configure the server firewall
configure_firewall() {
  log INFO "[*] Configuring firewall rules..."

  fw_file=$(get_workspace_resource_template_file "$WORKSPACE_DATA" "$RESOURCE_NAME")
  if [[ -z "$fw_file" ]]; then
    log ERROR "[X] No firewall template file found for resource ID: $RESOURCE_NAME" >&2
    return 1
  fi
  fw_file="$SC

  if [[ validate_template_firewall_file "$fw_file" ]]; then
    log ERROR "[X] Invalid firewall template file: $fw_file" >&2
    return 1
  fi

  if ! command -v ufw &> /dev/null; then
    log INFO "[*] ... Installing UFW ..."
    apt-get install -y ufw
    log INFO "[*] ... Installing UFW ...DONE"
  fi

  local reset=$(yq -r '.spec.reset' "$fw_file")
  if [[ "$reset" == "true" ]]; then
    echo "[INFO] Resetting firewall rules..."
    ufw --force reset
  fi

  log INFO "[*] ... Configuring UFW ..."

  # Apply default deny rules
  for rule in $(yq -c '.spec.defaults[]' "$fw_file"); do
    local direction=$(echo "$rule" | yq -r '.incoming // empty')
    if [[ -n "$direction" ]]; then
      echo "[INFO] Setting default incoming policy: $direction"
      ufw default "$direction" incoming
    fi

    direction=$(echo "$rule" | yq -r '.outgoing // empty')
    if [[ -n "$direction" ]]; then
      echo "[INFO] Setting default outgoing policy: $direction"
      ufw default "$direction" outgoing
    fi
  done

  # Internal function to apply one rule set (allow or deny)
  apply_rules() {
    local type="$1"  # "allow" or "deny"
    local rules key cmd

    mapfile -t rules < <(yq -c ".spec.${type}[]?" "$fw_file")

    for rule in "${rules[@]}"; do
      local direction proto port from to iface comment

      direction=$(echo "$rule" | yq -r '.direction')
      proto=$(echo "$rule" | yq -r '.proto // empty')
      port=$(echo "$rule" | yq -r '.port // empty')
      from=$(echo "$rule" | yq -r '.from // empty')
      to=$(echo "$rule" | yq -r '.to // empty')
      iface=$(echo "$rule" | yq -r '.interface // empty')
      comment=$(echo "$rule" | yq -r '.comment // empty')

      cmd="ufw $type"

      if [[ "$direction" == "in" && -n "$iface" ]]; then
        cmd+=" in on $iface"
      elif [[ "$direction" == "out" && -n "$iface" ]]; then
        cmd+=" out on $iface"
      elif [[ "$direction" == "in" ]]; then
        cmd+=" in"
      elif [[ "$direction" == "out" ]]; then
        cmd+=" out"
      fi

      [[ -n "$proto" ]] && cmd+=" proto $proto"
      [[ -n "$port" ]] && cmd+=" to any port $port"
      [[ -n "$from" ]] && cmd+=" from $from"
      [[ -n "$to" ]] && cmd+=" to $to"

      log INFO "[*] ...$cmd"
      eval "$cmd"
    done
  }

  # Allow and deny rules
  apply_rules "allow"
  apply_rules "deny"

  log INFO "[*] ... Configuring UFW ...DONE"

  # Enable firewall only if not active
  log INFO "[*] ... Configuring UFW ...DONE"
  if ! ufw status | grep -q "Status: active"; then
  log INFO "[*] ... Enabling UFW..."
  echo "y" | ufw enable
  log INFO "[*] ... Enabling UFW...DONE"
  fi

  log INFO "[*] ... Reloading UFW..."
  ufw reload
  ufw status verbose
  log INFO "[*] ... Reloading UFW...DONE"
}

# Function to prepare and mount disk volumes
# This function reads the workspace metadata to find disk information
# and mounts the disks according to the specified mount points.
# It also checks the disk sizes against expected values and formats them if necessary.
# The function assumes the workspace metadata is stored in a JSON file at $PATH_TEMP/$WORKSPACE.ws.json
# and that the disks are named in a specific pattern (e.g., /dev/sdb, /dev/sdc, etc.).
# The OS disk is identified by the root partition mounted at '/'.
# The function also ensures that the disks are formatted as ext4 and labeled according to the metadata.
# It creates mount points based on a template from the workspace metadata and ensures
# that the mount points are added to /etc/fstab for persistence across reboots.
configure_disks() {
  log INFO "[*] Preparing and mounting disk volumes..."

  # Identify the OS disk by partition mounted at root '/'
  log INFO "[*] ... Identify the OS disk by root mountpoint"
  local os_part=$(findmnt -n -o SOURCE /)
  local os_disk=$(lsblk -no PKNAME "$os_part")
  local os_disk_base="$os_disk"
  log INFO "[*] ... OS disk identified: /dev/$os_disk_base (root partition: $os_part)"

  # Get non-OS disks sorted by size
  log INFO "[*] ... Getting non-os disks"
  mapfile -t disks < <(lsblk -dn -o NAME,SIZE -b | \
    grep -v "^$os_disk_base$" | \
    grep -E '^sd[b-z]' | \
    sort -k2,2n -k1,1)

  # Insert OS disk as the first element
  os_size=$(lsblk -bn -o SIZE -d "/dev/$os_disk_base")
  disks=("$os_disk_base $os_size" "${disks[@]}")

  # Create disk name array
  declare -a disk_names
  for line in "${disks[@]}"; do
    disk_names+=("$(echo "$line" | awk '{print $1}')")
  done

  # Getting workspace disks
  local vm_file=$(get_workspace_resource_template_file "$WORKSPACE_DATA" "$RESOURCE_NAME")


  local disk_count=$(jq -r --arg id "$server_id" '.workspace.servers[] | select(.id == $id) | .disks | length' "$WORKSPACE_FILE")
  log INFO "[*] ... Found $disk_count disks for $HOSTNAME (including OS disk)"
  if (( ${#disk_names[@]} < disk_count )); then
    log ERROR "[!] Only found ${#disk_names[@]} disks but expected $disk_count"
    return 1
  fi

  log INFO "[+] Preparing and mounting disk volumes...DONE"
}

# Function to configure Docker Swarm
# This function initializes a new Swarm cluster if the current node is a manager,
# or joins an existing Swarm cluster if the current node is a worker.
# It retrieves the join tokens from the manager node and stores them in /tmp/manager_token.txt and /tmp/worker_token.txt.
# The function checks if the node is already part of a Swarm and skips initialization if it is.
# If the node is a manager, it initializes the Swarm and creates join tokens.
# If the node is a worker, it waits for the manager node to provide the join tokens before joining the Swarm.
# The function uses SSH to connect to the manager node to retrieve the tokens.
# It also ensures that the Swarm is configured with the correct advertise address based on the private IP of the node.
# Function that configures swarm servers and stores its tokens in /tmp and NOT /tmp/app
# Reason: /tmp/app gets cleaned !
configure_swarm() {
  log INFO "[*] Configuring Docker Swarm on $HOSTNAME..."

  if [ "$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null)" = "active" ]; then
    if [[ "$HOSTNAME" == *"$MANAGER_LABEL"* ]]; then
      log INFO "[*] Manager node already part of a Swarm. Creating join-tokens."
      docker swarm join-token manager -q > /tmp/manager_token.txt
      docker swarm join-token worker -q > /tmp/worker_token.txt
    else
      log INFO "[*] Node already part of a Swarm. Skipping initialization/joining."
    fi
    return
  fi

  if [[ "$HOSTNAME" == *"$MANAGER_LABEL"* ]]; then
    log INFO "[*] ... Initializing new Swarm cluster..."
    docker swarm init --advertise-addr "$PRIVATE_IP"
    mkdir -p /tmp/app
    chmod 1777 /tmp/app
    docker swarm join-token manager -q > /tmp/manager_token.txt
    docker swarm join-token worker -q > /tmp/worker_token.txt
    log INFO "[*] ... Saved manager and worker join tokens."
  else
    log INFO "[*] ... Joining existing Swarm cluster on $MANAGER_IP..."
    SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
    for i in {1..12}; do
      if ssh $SSH_OPTS root@$MANAGER_IP 'test -f /tmp/manager_token.txt && test -f /tmp/worker_token.txt'; then
        log INFO "[*] ... Swarm tokens are available on $MANAGER_IP"
        break
      fi
      log WARN "[!] ... Attempt $i: Waiting for Swarm tokens..."
      sleep 5
    done

    if ! ssh $SSH_OPTS root@$MANAGER_IP 'test -f /tmp/manager_token.txt && test -f /tmp/worker_token.txt'; then
      log ERROR "[x] Timed out waiting for Swarm tokens. Exiting."
      exit 1
    fi

    MANAGER_JOIN_TOKEN=$(ssh $SSH_OPTS root@$MANAGER_IP 'cat /tmp/manager_token.txt')
    WORKER_JOIN_TOKEN=$(ssh $SSH_OPTS root@$MANAGER_IP 'cat /tmp/worker_token.txt')

    if [[ "$HOSTNAME" == *"manager-"* ]]; then
      log INFO "[*] ... Joining as Swarm Manager..."
      docker swarm join --token "$MANAGER_JOIN_TOKEN" $MANAGER_IP:2377 --advertise-addr "$PRIVATE_IP"
    else
      log INFO "[*] ... Joining as Swarm Worker..."
      docker swarm join --token "$WORKER_JOIN_TOKEN" $MANAGER_IP:2377 --advertise-addr "$PRIVATE_IP"
    fi

    log INFO "[+] Successfully joined Swarm cluster"
  fi

  log INFO "[+] Configuring Docker Swarm on $HOSTNAME...DONE"
}

# Function to ensure Docker service is enabled and running
# This function checks if the Docker service is enabled and running,
# and starts it if necessary. It also enables the service to start on boot.
# It uses systemctl to manage the Docker service.
enable_docker() {
  log INFO "[*] Ensuring Docker is enabled and running..."

  # Enable docker service only if it's not already enabled
  if ! systemctl is-enabled --quiet docker; then
    log INFO "[*] ... Enabling Docker service..."
    systemctl enable docker
  else
    log INFO "[*] ... Docker service is already enabled."
  fi

  # Start docker service if not active
  if ! systemctl is-active --quiet docker; then
    log INFO "[*] ... Starting Docker service..."
    systemctl start docker
  else
    log INFO "[*] ... Docker service is already running."
  fi

  log INFO "[*] Ensuring Docker is enabled and running...DONE"
}

# Main function to initialize the remote server
main() {
  echo "[*] Initializing remote server..."
  cd /

  install_yq || {
    log ERROR "[X] Failed to install yq."
    exit 1
  }

  install_private_key || {
    log ERROR "[X] Failed to install private key."
    exit 1
  }

  install_docker || {
    log ERROR "[X] Failed to install Docker."
    exit 1
  }

  install_gluster || {
    log ERROR "[X] Failed to install GlusterFS."
    exit 1
  }

  configure_firewall || {
    log ERROR "[X] Failed to configure firewall."
    exit 1
  }

  configure_disks || {
    log ERROR "[X] Failed to mount disks."
    exit 1
  }

  configure_swarm || {
    log ERROR "[X] Failed to configure Docker Swarm."
    exit 1
  }

  enable_docker || {
    log ERROR "[X] Failed to enable Docker service."
    exit 1
  }

  echo "[*] Cleaning up swarm cluster..."
  # : "${PATH_TEMP:="/tmp/app"}"
  safe_rm_rf /tmp/app
  echo "[+] Remote server initialization completed."
}

main

#!/bin/bash
set -euo pipefail
HOSTNAME=$(hostname)
PATH_DEPLOY=$1

: "${PATH_DEPLOY:?Missing PATH_DEPLOY}"
if [[ ! -d "$PATH_DEPLOY" ]]; then
  echo "Temporary path $PATH_DEPLOY does not exist. Please create it or set a different path."
  exit 1
fi

# Available directories and files in $PATH_DEPLOY
# |- ./deploy/scripts/variables.env
# |- ./deploy/scripts/*
# |- ./deploy/workspaces/*

source "$PATH_DEPLOY/variables.env"
source "$PATH_DEPLOY/utilities.sh"

# Validate that the variables are set
: "${WORKSPACE:?Missing WORKSPACE}"
: "${MANAGER_IP:?Missing MANAGER_IP}"
: "${PRIVATE_IP:?Missing PRIVATE_IP}"

log INFO "[*] PATH_DEPLOY: $PATH_DEPLOY"
log INFO "[*] WORKSPACE: $WORKSPACE"
log INFO "[*] MANAGER_IP: $MANAGER_IP"
log INFO "[*] PRIVATE_IP: $PRIVATE_IP"

log INFO "[*] Finding workspace and manager information"
WORKSPACE_FILE=$(get_WORKSPACE_FILE "$PATH_DEPLOY" "$WORKSPACE") || exit 1
MANAGER_LABEL=$(get_manager_id "$WORKSPACE_FILE") || exit 1

export PRIVATE_IP="$PRIVATE_IP"
export MANAGER_IP="$MANAGER_IP"

# Function to log messages with a timestamp
install_private_key() {
  log INFO "[*] Installing uploaded private key..."
  mkdir -p ~/.ssh
  mv /root/.ssh/id_rsa_temp ~/.ssh/id_rsa
  chmod 600 ~/.ssh/id_rsa
  echo -e "Host *\n  StrictHostKeyChecking no\n" > ~/.ssh/config
  log INFO "[+] Installing uploaded private key...DONE"
}

# Function to configure the firewall using UFW
configure_firewall() {
  log INFO "[*] Configuring firewall..."
  
  if ! command -v ufw &> /dev/null; then
    log INFO "[*] ... Installing UFW ..."
    apt-get install -y ufw
    log INFO "[*] ... Installing UFW ...DONE"
  fi

  log INFO "[*] ... Configuring UFW ..."
  # Deny all traffic by default
  ufw --force reset
  ufw default deny incoming
  ufw default deny outgoing

  # Essential System Services
  ufw allow out 53/tcp comment 'DNS (TCP)'
  ufw allow out 53/udp comment 'DNS (UDP)'
  ufw allow out 123/udp comment 'NTP'

  # Loopback
  ufw allow in on lo comment 'Loopback IN'
  ufw allow out on lo comment 'Loopback OUT'

  # Package Management (Optional)
  ufw allow out 20,21/tcp comment 'FTP'
  ufw allow out 11371/tcp comment 'GPG keyserver'

  # Web & SSH
  ufw allow 22/tcp comment 'SSH'
  ufw allow 80/tcp comment 'HTTP'
  ufw allow 443/tcp comment 'HTTPS'
  ufw allow out 80/tcp comment 'HTTP'
  ufw allow out 443/tcp comment 'HTTPS'

  # SSH Outbound to internal nodes
  ufw allow out proto tcp to 10.0.0.0/23 port 22 comment 'SSH Outbound to internal nodes'

  # Docker Swarm management traffic (TCP) over VLAN
  ufw allow proto tcp from 10.0.0.0/23 to any port 2377 comment 'Swarm Control IN'
  ufw allow out proto tcp to 10.0.0.0/23 port 2377 comment 'Swarm Control OUT'

  # Docker VXLAN overlay network (UDP) over VLAN
  ufw allow proto udp from 10.0.0.0/23 to any port 4789 comment 'Swarm Overlay Network IN'
  ufw allow out proto udp to 10.0.0.0/23 port 4789 comment 'Swarm Overlay Network OUT'

  # Docker overlay network discovery (TCP + UDP) over VLAN
  ufw allow proto tcp from 10.0.0.0/23 to any port 7946 comment 'Swarm Discovery TCP'
  ufw allow proto udp from 10.0.0.0/23 to any port 7946 comment 'Swarm Discovery UDP'
  ufw allow out proto tcp to 10.0.0.0/23 port 7946 comment 'Swarm Gossip TCP OUT'
  ufw allow out proto udp to 10.0.0.0/23 port 7946 comment 'Swarm Gossip UDP OUT'

  # Postgres traffic between nodes
  ufw allow proto tcp from 10.0.0.0/23 to any port 5432 comment 'Postgres IN'
  ufw allow out proto tcp to 10.0.0.0/23 port 5432 comment 'Postgres OUT'

  # Redis traffic between nodes
  ufw allow proto tcp from 10.0.0.0/23 to any port 6379 comment 'Redis IN'
  ufw allow out proto tcp to 10.0.0.0/23 port 6379 comment 'Redis OUT'

  # Gluser traeffic between nodes
  ufw allow proto tcp from 10.0.0.0/23 to any port 24007:24008 comment 'GlusterFS Management IN'
  ufw allow proto tcp from 10.0.0.0/23 to any port 49152:49251 comment 'GlusterFS Bricks IN'
  ufw allow out proto tcp to 10.0.0.0/23 port 24007:24008 comment 'GlusterFS Management OUT'
  ufw allow out proto tcp to 10.0.0.0/23 port 49152:49251 comment 'GlusterFS Bricks OUT'
  
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

  log INFO "[+] Configuring firewall...DONE"
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
mount_disks() {
  log INFO "[*] Preparing and mounting disk volumes..."
  local server_id=$(get_server_id "$WORKSPACE_FILE" "$HOSTNAME") || exit 1

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

  local disk_count=$(jq -r --arg id "$server_id" '.servers[] | select(.id == $id) | .disks | length' "$WORKSPACE_FILE")
  log INFO "[*] ... Found $disk_count disks for $HOSTNAME (including OS disk)"
  if (( ${#disk_names[@]} < disk_count )); then
    log ERROR "[!] Only found ${#disk_names[@]} disks but expected $disk_count"
    return 1
  fi

  local mount_template=$(jq -r --arg id "$server_id" '.servers[] | select(.id == $id) | .mountpoint' "$WORKSPACE_FILE")

  log INFO "[*] Looping over all disks"
  for i in $(seq 0 $((disk_count - 1))); do
    log INFO "[*] Mounting disk $i for $HOSTNAME"

    local disk="/dev/${disk_names[$i]}"
    local label=$(jq -r --arg id "$server_id" --argjson i "$i" \
      '.servers[] | select(.id == $id) | .disks[$i].label' "$WORKSPACE_FILE")
    local part=""
    local fs_type=""
    local current_label=""
    local mnt="${mount_template//\$\{disk\}/$((i + 1))}"

    if [[ $i -eq 0 ]]; then
      # OS disk — use the actual root partition, not just first partition
      part="$os_part"
      fs_type=$(blkid -s TYPE -o value "$part" 2>/dev/null || echo "")
      current_label=$(blkid -s LABEL -o value "$part" 2>/dev/null || echo "")
      log INFO "[*] ... Checking OS disk label on $part (expected label=$label)"
      if [[ "$fs_type" != "ext4" ]]; then
        log WARN "[!] OS disk has unexpected FS type ($fs_type), skipping label check"
        continue
      elif [[ "$current_label" != "$label" ]]; then
        log INFO "[*] ... Relabeling OS disk from $current_label to $label"
        e2label "$part" "$label"
      else
        log INFO "[*] ... OS disk label is already correct: $label"
      fi
      # Make sure the mountpoint exist
      mkdir -p "$mnt"
      continue
    else
      # Data disks — expect partition 1 on the disk (e.g., /dev/sdb1)
      part=$(lsblk -nr -o NAME "$disk" | awk 'NR==2 {print "/dev/" $1}')
      fs_type=$(blkid -s TYPE -o value "$part" 2>/dev/null || echo "")
      current_label=$(blkid -s LABEL -o value "$part" 2>/dev/null || echo "")
    fi

    log INFO "[*] ... Mounting data disk $i for $HOSTNAME"
    log INFO "[*] ... Preparing disk $disk (label=$label)"

    # Get expected disk size from workspace metadata (in GB)
    local expected_size_gb=$(jq -r --arg id "$server_id" --argjson i "$i" \
      '.servers[] | select(.id == $id) | .disks[$i].size' "$WORKSPACE_FILE")
    
    # Get actual disk size in bytes and convert to GB (rounding down)
    local actual_size_bytes=$(lsblk -bn -o SIZE -d "$disk")
    local actual_size_gb=$(( actual_size_bytes / 1024 / 1024 / 1024 ))

    log INFO "[*] ... Validating size for $disk: expected ${expected_size_gb}GB, found ${actual_size_gb}GB"

    if disk_size_matches "$actual_size_gb" "$expected_size_gb"; then
      log INFO "[*] ... Disk size for $disk matches expected ${expected_size_gb}GB, found ${actual_size_gb}GB"
    else
      log ERROR "[!] Disk size mismatch for $disk — expected ${expected_size_gb}GB, got ${actual_size_gb}GB"
      continue  # Skip this disk to avoid accidental mount/format
    fi

    # Check if partition exists (lsblk part)
    if ! lsblk "$part" &>/dev/null; then
      log INFO "[*] ... Partitioning $disk"
      parted -s "$disk" mklabel gpt
      parted -s -a optimal "$disk" mkpart primary ext4 0% 100%
      sync
      sleep 5
      # refresh fs_type and current_label after new partition creation
      part="/dev/$(lsblk -nro NAME "$disk" | sed -n '2p')"
      fs_type=$(blkid -s TYPE -o value "$part" 2>/dev/null || echo "")
      current_label=$(blkid -s LABEL -o value "$part" 2>/dev/null || echo "")
    else
      log INFO "[*] ... Skipping partitioning: $disk already partitioned"
    fi

    if [[ -z "$fs_type" ]]; then
      log INFO "[*] ... Formatting $part as ext4 with label $label"
      mkfs.ext4 -L "$label" "$part"
    elif [[ "$fs_type" != "ext4" ]]; then
      log WARN "[!] $part has unexpected FS type ($fs_type), skipping"
      continue
    elif [[ "$current_label" != "$label" ]]; then
      log INFO "[*] ... Relabeling $part from $current_label to $label"
      e2label "$part" "$label"
    else
      log INFO "[*] ... $part already formatted and labeled $label"
    fi

    mkdir -p "$mnt"
    if ! mountpoint -q "$mnt"; then
      log INFO "[*] ... Mounting $label to $mnt"
      mount "/dev/disk/by-label/$label" "$mnt"
    else
      log INFO "[+] ... Already mounted: $mnt"
    fi

    # Ensure persistence in fstab (idempotent)
    fstab_line="LABEL=$label $mnt ext4 defaults 0 2"
    if grep -qE "^\s*LABEL=$label\s" /etc/fstab; then
      if ! grep -Fxq "$fstab_line" /etc/fstab; then
        log INFO "[*] ... Updating existing fstab entry for $label"
        sed -i.bak "/^\s*LABEL=$label\s/c\\$fstab_line" /etc/fstab
      else
        log INFO "[*] ... fstab entry for $label is already correct"
      fi
    else
      log INFO "[*] ... Adding fstab entry for $label"
      echo "$fstab_line" >> /etc/fstab
    fi

    log INFO "[*] ... Disk $i mounted successfully at $mnt"
  done

  log INFO "[+] All disks prepared and mounted."
}

# Function to install Docker if not already installed
install_docker_if_needed() {
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
enable_docker_service() {
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

# Function to install GlusterFS if not already installed
# This function checks if the GlusterFS client is installed,
# and if not, installs it using the package manager.
# It also ensures that the GlusterFS service is enabled and started.
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

# Main function to initialize the remote server
main() {
    echo "[*] Initializing remote server..."
    cd /

    install_private_key || {
      log ERROR "[X] Failed to install private key."
      exit 1
    }

    configure_firewall || {
      log ERROR "[X] Failed to configure firewall."
      exit 1
    }

    mount_disks || {
      log ERROR "[X] Failed to mount disks."
      exit 1
    }

    install_docker_if_needed || {
      log ERROR "[X] Failed to install Docker."
      exit 1
    }

    configure_swarm || {
      log ERROR "[X] Failed to configure Docker Swarm."
      exit 1
    }

    enable_docker_service || {
      log ERROR "[X] Failed to enable Docker service."
      exit 1
    }

    install_gluster || {
      log ERROR "[X] Failed to install GlusterFS."
      exit 1
    }
    
    echo "[*] Remote server cleanup..."
    rm -rf /tmp/app/*   # : "${PATH_TEMP:="/tmp/app"}"
    echo "[+] Remote server initialization completed."
    echo "[*] Cleaning up swarm cluster..."
  rm -rf "$VAR_PATH_TEMP/*"
}

main


















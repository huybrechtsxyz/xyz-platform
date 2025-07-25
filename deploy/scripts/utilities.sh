#!/bin/bash

# Logging function
log() {
  local level="$1"; shift
  local msg="$*"
  local timestamp
  timestamp=$(date +"%Y-%m-%d %H:%M:%S")
  case "$level" in
    INFO)    echo -e "[\033[1;34mINFO\033[0m]  - $msg" ;;
    WARN)    echo -e "[\033[1;33mWARN\033[0m]  - $msg" ;;
    ERROR)   echo -e "[\033[1;31mERROR\033[0m] - $msg" >&2 ;;
    *)       echo -e "[UNKNOWN] - $msg" ;;
  esac
}

# Generate an environment file only taking env vars with specific prefix
# Usage: generate_env_file <PREFIX> <OUTPUT_FILE>
# Example: generate_env_file MYAPP_ /path/to/output.env
# This function will create an environment file with all variables starting with the given prefix.
# It will strip the prefix from the variable names in the output file.
# It will also ensure that all variables are non-empty before writing to the file.
generate_env_file() {
  local prefix="$1"
  local output_file="$2"

  if [[ -z "$prefix" || -z "$output_file" ]]; then
    echo "[!] Usage: generate_env_file <PREFIX> <OUTPUT_FILE>" >&2
    return 1
  fi

  log INFO "[*] Generating environment file for variables with prefix '$prefix'..."

  # Get all variables starting with the prefix
  mapfile -t vars < <(compgen -v | grep "^${prefix}")

  if [[ "${#vars[@]}" -eq 0 ]]; then
    echo "[!] Error: No environment variables found with prefix '$prefix'" >&2
    return 1
  fi

  # Validate all are non-empty
  for var in "${vars[@]}"; do
    [[ -z "${!var}" ]] && { echo "[!] Error: Missing required variable '$var'" >&2; return 1; }
  done

  # Ensure output directory exists
  mkdir -p "$(dirname "$output_file")"

  # Generate the env file with the prefix stripped
  {
    echo "# Auto-generated environment file (prefix '$prefix' stripped)"
    for var in "${vars[@]}"; do
      short_var="${var#$prefix}"
      printf '%s=%q\n' "$short_var" "${!var}"
    done
  } > "$output_file"

  log INFO "[+] Environment file generated at '$output_file'"
}

# Function to create a Docker network if it doesn't exist
# Usage: create_docker_network <network_name>
# Example: create_docker_network my_overlay_network
# This function checks if a Docker overlay network exists and creates it if not.
# It requires Docker to be running in Swarm mode.
create_docker_network() {
  local network="$1"
  log INFO "[*] Ensuring Docker network '$network' exists..."

  if docker network inspect "$network" --format '{{.Id}}' &>/dev/null; then
    log INFO "[=] ... Docker network '$network' already exists."
  else
    log INFO "[+] ... Creating Docker overlay network '$network'..."
    if docker network create --driver overlay "$network"; then
      log INFO "[+] ... Docker network '$network' created successfully."
    else
      log ERROR "[X] Failed to create Docker network '$network'. Is Docker Swarm mode enabled?"
      return 1
    fi
  fi
}

# Function to create a Docker secret
# Usage: create_docker_secret <label> <name> <value>
# Example: create_docker_secret my_secret my_secret_name "my_secret_value"
# This function creates a Docker secret if it doesn't already exist.
# If the secret already exists and is in use, it will skip deletion and creation.
# If the secret exists but is not in use, it will remove the old secret and create a new one.
# It also checks if the secret is in use before attempting to remove it.
create_docker_secret() {
  local label="$1"
  local name="$2"
  local value="$3"

  log INFO "[*] ... Processing secret: $label"

  if [[ -z "$name" ]]; then
    log WARN "[!] ... Secret name is not defined for $label. Skipping."
    return 1
  fi

  if [[ -z "$value" ]]; then
    log WARN "[!] ... Secret value for '$name' is empty. Skipping."
    return 0
  fi

  if docker secret inspect "$name" &>/dev/null; then
    log INFO "[*] ... Secret '$name' already exists."

    if is_secret_in_use "$name"; then
      log INFO "[*] ... Secret '$name' is in use. Skipping deletion and creation."
      return 0
    fi

    log INFO "[*] ... Removing old secret '$name'..."
    if ! docker secret rm "$name"; then
      log WARN "[!] Could not remove secret '$name' (possibly still in use). Skipping recreate."
      return 0
    fi
  fi

  # Use printf to avoid trailing newline
  if printf "%s" "$value" | docker secret create "$name" -; then
    log INFO "[+] ... Secret '$name' created."
  else
    log ERROR "[X] ... Failed to create secret '$name'."
    return 1
  fi
}

# Function to check if a Docker secret is in use
# Usage: is_secret_in_use <secret_name>
# Example: is_secret_in_use my_secret
# This function checks if a Docker secret is currently in use by any service or container.
is_secret_in_use() {
  local secret_name="$1"
  local usage_found=0

  # Check services using the secret
  local services_using
  services_using=$(docker service ls --format '{{.Name}}' | \
    xargs -r -n1 -I{} docker service inspect {} --format '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}{{if eq .SecretName "'"$secret_name"'"}}{{$.Spec.Name}}{{end}}{{end}}' 2>/dev/null | grep -v '^$' || true)

  if [[ -n "$services_using" ]]; then
    log INFO "[*] ... Secret '$secret_name' is in use by Docker service(s): $services_using"
    usage_found=1
  fi

  # Check containers using the secret (running standalone containers might mount secrets differently)
  local containers_using
  containers_using=$(docker ps --format '{{.ID}}' | \
    xargs -r -n1 -I{} docker inspect {} --format '{{range .Mounts}}{{if and (eq .Type "bind") (hasPrefix .Source "/var/lib/docker/swarm/secrets/")}}{{.Name}}{{end}}{{end}}' 2>/dev/null | \
    grep -w "$secret_name" || true)

  if [[ -n "$containers_using" ]]; then
    log INFO "[*] ... Secret '$secret_name' is in use by running container(s)."
    usage_found=1
  fi

  if [[ $usage_found -eq 1 ]]; then
    return 0
  else
    log INFO "[*] ... Secret '$secret_name' is not in use."
    return 1
  fi
}

# Function to load Docker secrets from a file
# Usage: load_docker_secrets <secrets_file>
# Example: load_docker_secrets /path/to/secrets.env
# This function reads a file containing key-value pairs (one per line) and creates Docker secrets
# It skips blank lines and comments, trims whitespace, and removes surrounding quotes from values.
# It also checks if the secret already exists and is in use before attempting to create it.
# If the secret exists but is not in use, it will remove the old secret and create a new one.
load_docker_secrets() {
  
  local secrets_file=${1:-}

  log INFO "[*] Loading secrets from $secrets_file..."

  if [[ ! -f "$secrets_file" ]]; then
    log ERROR "[x] Secrets file not found: $secrets_file"
    return 1
  fi

  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    # Skip blank lines or comments
    [[ -z "$key" || "$key" =~ ^\s*# ]] && continue

    # Trim whitespace
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)

    # Remove surrounding quotes from value
    value="${value%\"}"
    value="${value#\"}"

    create_docker_secret "$key" "$key" "$value"
  done < "$secrets_file"

  echo "[+] Finished loading secrets."
}

# Function to get the Terraform output file path
# Usage: get_terraform_file <TEMP_PATH>
# Example: get_terraform_file /tmp/workspace
# This function checks if the Terraform output file exists in the specified temporary path.
# If it does, it returns the full path to the Terraform file.
# If the file does not exist, it logs an error and returns 1.
get_terraform_file() {
  local TEMP_PATH="$1"

  if [[ -z "$TEMP_PATH" ]]; then
    log ERROR "[!] get_terraform_file requires TEMP_PATH argument."
    return 1
  fi

  local TF_FILE="$TEMP_PATH/terraform.json"

  if [[ ! -f "$TF_FILE" ]]; then
    log ERROR "[!] Terraform output file not found: $TF_FILE"
    return 1
  fi

  echo "$TF_FILE"
}

# Function to get Terraform data based on label and data type
# Usage: get_terraform_data <terraform_file> <label> [data_type]
# Example: get_terraform_data /tmp/workspace/terraform.json my_label private_ip
# This function reads the specified Terraform file and extracts data for the given label.
# If data_type is not specified, it defaults to "private_ip".
get_terraform_data() {
  local terraform_file="$1"
  local label="$2"
  local data_type="${3:-private_ip}"  # default to private_ip if not specified

  if [[ ! -f "$terraform_file" ]]; then
    log ERROR "[!] Terraform file not found: $terraform_file"
    return 1
  fi

  local result
  result=$(jq -r --arg label "$label" --arg data_type "$data_type" \
    '.include[] | select(.label == $label) | .[$data_type]' "$terraform_file") || {
      log ERROR "[!] Unable to retrieve $data_type for $label in $terraform_file"
      return 1
    }

  if [[ -z "$result" || "$result" == "null" ]]; then
    log ERROR "[!] Empty or null $data_type for $label in $terraform_file"
    return 1
  fi

  echo "$result"
}

# Function to get the workspace file path
# Usage: get_workspace_file <TEMP_PATH> <WORKSPACE_NAME>
# Example: get_workspace_file /tmp/workspace my_workspace
# This function checks if the workspace file exists in the specified temporary path.
# If it does, it returns the full path to the workspace file.
# If the file does not exist, it logs an error and returns 1.
get_workspace_file() {
  local TEMP_PATH="$1"
  local WORKSPACE_NAME="$2"

  if [[ -z "$TEMP_PATH" || -z "$WORKSPACE_NAME" ]]; then
    log ERROR "[!] get_workspace_file requires TEMP_PATH and WORKSPACE_NAME arguments."
    return 1
  fi

  local WORKSPACE_FILE="$TEMP_PATH/$WORKSPACE_NAME.ws.json"

  if [[ ! -f "$WORKSPACE_FILE" ]]; then
    log ERROR "[!] Workspace definition file not found: $WORKSPACE_FILE"
    return 1
  fi

  echo "$WORKSPACE_FILE"
}

# Function to get the server ID from the workspace file based on the hostname
# Usage: get_server_id <WORKSPACE_FILE> <HOSTNAME>
# Example: get_server_id /tmp/workspace/my_workspace.ws.json my_hostname
# This function reads the workspace file and extracts the server ID that matches the given hostname.
# If no matching server ID is found, it logs an error and returns 1.
get_server_id() {
  local WORKSPACE_FILE="$1"
  local HOSTNAME="$2"

  if [[ -z "$WORKSPACE_FILE" || -z "$HOSTNAME" ]]; then
    log ERROR "[!] get_server_id requires WORKSPACE_FILE and HOSTNAME arguments."
    return 1
  fi

  local SERVER_ID=$(jq -r '.servers[].id' "$WORKSPACE_FILE" | while read -r id; do
    if [[ "$HOSTNAME" == *"$id"* ]]; then
      echo "$id"
      break
    fi
  done)

  if [[ -z "$SERVER_ID" ]]; then
    log ERROR "[!] No matching server ID found for hostname: $HOSTNAME"
    return 1
  fi

  echo "$SERVER_ID"
}

# Function to get the main manager ID from the workspace file
# This is always the first server defined in the workspace
# Usage: get_manager_id <WORKSPACE_FILE>
# Example: get_manager_id /tmp/workspace/my_workspace.ws.json
# This function reads the workspace file and extracts the main manager ID.
# If no main manager ID is found, it logs an error and returns 1.
get_manager_id() {
  local WORKSPACE_FILE="$1"

  if [[ -z "$WORKSPACE_FILE" ]]; then
    log ERROR "[!] get_manager_id requires WORKSPACE_FILE argument."
    return 1
  fi

  if [[ ! -f "$WORKSPACE_FILE" ]]; then
    log ERROR "[!] Workspace definition file not found: $WORKSPACE_FILE"
    return 1
  fi

  local MAIN_MANAGER_ID=$(jq -r '.servers[0].id' "$WORKSPACE_FILE")

  if [[ -z "$MAIN_MANAGER_ID" || "$MAIN_MANAGER_ID" == "null" ]]; then
    log ERROR "[!] No main manager ID found in workspace file."
    return 1
  fi

  echo "$MAIN_MANAGER_ID"
}

# Function to validite the volume configuration
# Usage: validate_volume_configuration <volumename> <volumetype> <bricks[]>
# Example validate_volume_configuration "$volumename" "$volumetype" "${bricks[@]}"
validate_volume_configuration() {
  local volname="$1"
  local voltype="$2"
  shift 2
  local bricks=("$@")
  local brick_count="${#bricks[@]}"

  log INFO "[*] Validating configuration for volume '$volname' of type '$voltype' with $brick_count bricks"

  # Check for duplicate brick paths
  local unique_count
  unique_count=$(printf "%s\n" "${bricks[@]}" | sort -u | wc -l)
  if [[ "$unique_count" -ne "$brick_count" ]]; then
    log ERROR "[!] Duplicate brick paths detected in volume '$volname'"
    return 1
  fi

  if [[ "$voltype" == "replicated" ]]; then
    if (( brick_count < 2 )); then
      log ERROR "[!] Replicated volume requires at least 2 bricks (found $brick_count)"
      return 1
    elif (( brick_count == 2 )); then
      log WARN "[!] Replica 2 volumes are prone to split-brain. Consider Replica 3 or thin-arbiter."
    fi
  elif [[ "$voltype" == "distributed" ]]; then
    if (( brick_count < 1 )); then
      log ERROR "[!] Distributed volume requires at least 1 brick."
      return 1
    fi
  else
    log ERROR "[!] Unsupported volume type: $voltype"
    return 1
  fi

  log INFO "[+] Volume '$volname' configuration validated successfully."
}

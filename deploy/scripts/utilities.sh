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

# Function to load secret identifiers from a JSON file
# Usage: load_secret_identifiers <path-to-ws.json> <environment>
# Example: load_secret_identifiers /path/to/workspace.json dev
# This function exports environment variables in the format UUID_<SECRET_NAME> with their values.
load_secret_identifiers() {
  # Workspace is required
  FILE="$1"
  ENVIRONMENT="$2"

  if [ -z "$FILE" ] || [ -z "$ENVIRONMENT" ]; then
    echo "Usage: load_secret_identifiers <path-to-ws.json> <environment>"
    return 1
  fi

  echo "[*] ... Loading secrets for environment '$ENVIRONMENT' from '$FILE'..."

  if ! command -v jq >/dev/null 2>&1; then
    echo "Error: 'jq' is required but not installed."
    exit 1
  fi

  if [ ! -f "$FILE" ]; then
    echo "Error: JSON file '$FILE' not found."
    exit 1
  fi

  # Extract secrets for the specified environment
  secrets=$(jq -r --arg env "$ENVIRONMENT" '.secrets.environment[$env]' "$FILE")
  if [ "$secrets" == "null" ]; then
    echo "[!] ERROR No secrets found for environment '$ENVIRONMENT'."
    return 1
  fi

  # Iterate and export
  echo "$secrets" | jq -r 'to_entries[] | "\(.key)=\(.value)"' | while IFS="=" read -r key value; do
    UPPER_KEY=$(echo "$key" | tr '[:lower:]' '[:upper:]')
    CLEAN_VALUE=$(echo "$value" | sed 's/^"//;s/"$//')
    export "UUID_${UPPER_KEY}"="$CLEAN_VALUE"
    echo "Exported UUID_${UPPER_KEY}"
  done

  echo "[*] ... Loading secrets for environment '$ENVIRONMENT' from '$FILE'...DONE"
}

# Function to create a Docker network if it doesn't exist
# Usage: createnetwork <network_name>
# Example: createnetwork my_overlay_network
# This function checks if a Docker overlay network exists and creates it if not.
# It requires Docker to be running in Swarm mode.
createnetwork() {
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
# Usage: createdockersecret <label> <name> <value>
# Example: createdockersecret my_secret my_secret_name "my_secret_value"
# This function creates a Docker secret if it doesn't already exist.
# If the secret already exists and is in use, it will skip deletion and creation.
# If the secret exists but is not in use, it will remove the old secret and create a new one.
# It also checks if the secret is in use before attempting to remove it.
createdockersecret() {
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

    if issecretinuse "$name"; then
      log INFO "[*] ... Secret '$name' is in use. Skipping deletion and creation."
      return 0
    fi

    log INFO "[*] ... Removing old secret '$name'..."
    docker secret rm "$name"
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
# Usage: issecretinuse <secret_name>
# Example: issecretinuse my_secret
# This function checks if a Docker secret is currently in use by any service or container.
issecretinuse() {
  local secret_name="$1"

  # Check services using the secret
  if docker service ls --format '{{.Name}}' | \
    xargs -r -n1 -I{} docker service inspect {} --format '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}{{if eq .SecretName "'"$secret_name"'"}}1{{end}}{{end}}' 2>/dev/null | \
    grep -q "1"; then
    log INFO "[*] ... Secret '$secret_name' is in use by a Docker service."
    return 0
  fi

  # Check containers using the secret (Swarm services mount secrets as /run/secrets/*)
  if docker ps --format '{{.ID}}' | \
    xargs -r -n1 -I{} docker inspect {} --format '{{range .Mounts}}{{if and (eq .Type "bind") (hasPrefix .Source "/var/lib/docker/swarm/secrets/")}}{{.Name}}{{end}}{{end}}' 2>/dev/null | \
    grep -q "$secret_name"; then
    log INFO "[*] ... Secret '$secret_name' is in use by a running container."
    return 0
  fi

  log INFO "[*] ... Secret '$secret_name' is not in use."
  return 1
}

# Function to load Docker secrets from a file
# Usage: loaddockersecrets <secrets_file>
# Example: loaddockersecrets /path/to/secrets.env
# This function reads a file containing key-value pairs (one per line) and creates Docker secrets
# It skips blank lines and comments, trims whitespace, and removes surrounding quotes from values.
# It also checks if the secret already exists and is in use before attempting to create it.
# If the secret exists but is not in use, it will remove the old secret and create a new one.
loaddockersecrets() {
  
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

    createdockersecret "$key" "$key" "$value"
  done < "$secrets_file"

  echo "[+] Finished loading secrets."
}

#!/bin/bash
#===============================================================================
# Script Name   : utilities.sh
# Description   : Different modular and reusable functions
# Usage         : n.a.
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

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

# Function to check if the actual disk size matches the expected size within a tolerance
disk_size_matches() {
  local actual_gb="$1"        # e.g. 39
  local expected_gb="$2"      # e.g. 40
  local tolerance_mb="${3:-20}"  # Optional, default to 20 MiB

  local BYTES_PER_GB=1073741824
  local BYTES_PER_MB=1048576

  local expected_bytes=$(( expected_gb * BYTES_PER_GB ))
  local actual_bytes=$(( actual_gb * BYTES_PER_GB ))
  local diff_bytes=$(( actual_bytes - expected_bytes ))
  local diff_mb=$(( diff_bytes / BYTES_PER_MB ))
  local abs_diff_mb=${diff_mb#-}

  if (( abs_diff_mb <= tolerance_mb )); then
    return 0  # Match within tolerance
  else
    return 1  # Too far off
  fi
}

#===============================================================================
# Environment variables and secrets
#===============================================================================

# Function to export secrets from a YAML file using yq and Bitwarden CLI
# Usage: export_secrets <YAML_FILE> [<YQ_PATH>] [<PREFIX>] [<OUTPUT_FILE>] [<CASING[upper:lower]>]
# CASING: upper | lower (optional; default: as-is)
export_secrets() {
  local yaml_file="$1"
  local yq_path="${2:-.spec.secrets}"
  local prefix="${3:-SECRET_}"
  local output_file="$4"
  local casing="${5:-}"

  log INFO "[*] ...Starting export of secrets from $yaml_file at path '$yq_path' with prefix '$prefix' and casing '$casing'"

  if [[ -z "$yaml_file" || ! -f "$yaml_file" ]]; then
    log ERROR "[X] Missing or invalid YAML file: $yaml_file" >&2
    return 1
  fi

  export BWS_ACCESS_TOKEN="${BITWARDEN_TOKEN:?Missing BITWARDEN_TOKEN environment variable}"

  local count
  count=$(yq e "$yq_path | length" "$yaml_file" 2>/dev/null)
  if [[ "$count" == "0" || "$count" == "null" ]]; then
    log WARN "[!] No secrets found at path '$yq_path' in $yaml_file" >&2
    return 0
  fi

  # Prepare output file if specified
  if [[ -n "$output_file" ]]; then
    mkdir -p "$(dirname "$output_file")"
    > "$output_file"
  fi

  for ((i = 0; i < count; i++)); do
    local key source id value data varname

    key=$(yq e "$yq_path[$i].key" "$yaml_file")
    source=$(yq e "$yq_path[$i].source" "$yaml_file")
    id=$(yq e "$yq_path[$i].value" "$yaml_file")

    if [[ "$source" != "bitwarden" ]]; then
      log WARN "[!] Skipping unsupported secret source: $source"
      continue
    fi

    data=$(bws secret get "$id" --output json 2>/dev/null || true)

    if [[ -z "$data" ]]; then
      log ERROR "[X] Failed to fetch secret for $key (id: $id)" >&2
      continue
    fi

    value=$(jq -r '.value' <<< "$data")

    # Apply casing to varname
    case "$casing" in
      upper)
        varname="${prefix}$(echo "$key" | tr '[:lower:]' '[:upper:]')"
        ;;
      lower)
        varname="${prefix}$(echo "$key" | tr '[:upper:]' '[:lower:]')"
        ;;
      *)
        varname="${prefix}${key}"
        ;;
    esac

    export "$varname=$value"

    if [[ -n "$output_file" ]]; then
      echo "$varname=${value@Q}" >> "$output_file"
    fi

    log INFO "[+] ......Exported $varname"
  done

  log INFO "[+] ...Export completed for $count secrets from $yaml_file"
}

# Function to export variables from a YAML file using yq
# Usage: export_variables <YAML_FILE> [<YQ_PATH>] [<PREFIX>] [<OUTPUT_FILE>] [<CASING[upper:lower]>]
# CASING: upper | lower (optional; default: as-is)
export_variables() {
  local yaml_file="$1"
  local yq_path="${2:-.spec.variables}"
  local prefix="${3:-VAR_}"
  local output_file="$4"
  local casing="${5:-}"

  log INFO "[*] ...Starting export of variables from $yaml_file at path '$yq_path' with prefix '$prefix' and casing '$casing'"

  if [[ -z "$yaml_file" || ! -f "$yaml_file" ]]; then
    log ERROR "[X] Missing or invalid YAML file: $yaml_file" >&2
    return 1
  fi

  local count
  count=$(yq e "$yq_path | length" "$yaml_file" 2>/dev/null)
  if [[ "$count" == "0" || "$count" == "null" ]]; then
    log WARN "[!] No variables found at path '$yq_path' in $yaml_file" >&2
    return 0
  fi

  # Prepare output file if specified
  if [[ -n "$output_file" ]]; then
    mkdir -p "$(dirname "$output_file")"
    > "$output_file"
  fi

  for ((i = 0; i < count; i++)); do
    local key source id value data varname

    key=$(yq e "$yq_path[$i].key" "$yaml_file")
    value=$(yq e "$yq_path[$i].value" "$yaml_file")

    if [[ -z "$value" ]]; then
      log ERROR "[X] Failed to fetch variable for $key (id: $value)" >&2
      continue
    fi

    # Apply casing to varname
    case "$casing" in
      upper)
        varname="${prefix}$(echo "$key" | tr '[:lower:]' '[:upper:]')"
        ;;
      lower)
        varname="${prefix}$(echo "$key" | tr '[:upper:]' '[:lower:]')"
        ;;
      *)
        varname="${prefix}${key}"
        ;;
    esac

    export "$varname=$value"

    if [[ -n "$output_file" ]]; then
      echo "$varname=${value@Q}" >> "$output_file"
    fi

    log INFO "[+] ......Exported $varname"
  done

  log INFO "[+] ...Export completed for $count variables from $yaml_file"
}

# Function to load a script and handle errors
# Usage: load_script <script_path>
load_script() {
  local script_path="$1"
  if [[ -f "$script_path" ]]; then
    source "$script_path"
    log INFO "[*] ...Loaded $script_path"
  else
    log ERROR "[X] Missing $(basename "$script_path") at $(dirname "$script_path")"
    exit 1
  fi
}

# Function to load a source file and handle errors
# Usage: load_source <source_path>
# This function checks if the source file exists and sources it.
# If the file does not exist, it logs an error and exits.
load_source() {
  local source_path="$1"
  if [[ -f "$source_path" ]]; then
    +a
    source "$source_path"
    -a
    log INFO "[*] ...Sourced $source_path"
  else
    log ERROR "[X] Missing $(basename "$source_path") at $(dirname "$source_path")"
    exit 1
  fi
}

#===============================================================================






#===============================================================================





















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
    echo "[!] Warning: No environment variables found with prefix '$prefix'" >&2
    return 0
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



# Merge two environment files
# This function preserves the values from the first file, duplicate keys in the second file are ignored.
# If OUTPUT_FILE is provided (even same as FILE1), merged result is written to it.
merge_env_file() {
  local FILE1="$1"
  local FILE2="$2"
  local OUTPUT_FILE="$3"

  if [[ -z "$FILE1" || -z "$FILE2" || ! -f "$FILE1" || ! -f "$FILE2" ]]; then
    echo "Usage: merge_env_file <FILE1> <FILE2> [OUTPUT_FILE]"
    return 1
  fi

  local TMP_OUTPUT
  TMP_OUTPUT=$(mktemp)

  declare -A env_map

  # Load first file — values from here are preserved
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    key=$(echo "$key" | xargs)
    env_map["$key"]="$value"
  done < "$FILE1"

  # Load second file — skip keys that already exist
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    key=$(echo "$key" | xargs)
    if [[ -z "${env_map[$key]+_}" ]]; then
      env_map["$key"]="$value"
    fi
  done < "$FILE2"

  # Write to temporary file first
  for key in "${!env_map[@]}"; do
    echo "$key=${env_map[$key]}"
  done | sort > "$TMP_OUTPUT"

  # Move temp file to output (or FILE1 if output not specified)
  if [[ -n "$OUTPUT_FILE" ]]; then
    mv "$TMP_OUTPUT" "$OUTPUT_FILE"
  else
    cat "$TMP_OUTPUT"
    rm "$TMP_OUTPUT"
  fi
}

# Save force removal function
# As a safety precaution, check that the path you're about to wipe isn't / or empty
safe_rm_rf() {
  local path="$1"

  if [[ -z "$path" || "$path" == "/" ]]; then
    log WARN "[!] Skipped unsafe or empty path: '$path'"
    return 1
  fi

  local real_path
  real_path=$(realpath -m "$path")

  if [[ "$real_path" == "/" ]]; then
    log ERROR "[X] Refusing to remove root directory"
    return 1
  fi

  if [[ -f "$real_path" ]]; then
    log INFO "[*] Removing file: $real_path"
    rm -f "$real_path"
  elif [[ -d "$real_path" ]]; then
    log INFO "[*] Removing directory contents: $real_path"
    shopt -s nullglob dotglob
    rm -rf "$real_path"/*
    shopt -u nullglob dotglob
  else
    log WARN "[!] Skipped non-existent path: $real_path"
    return 0
  fi
}

#================================================================================
# Docker utility functions
#================================================================================

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
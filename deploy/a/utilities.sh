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

# Function to export secrets from a YAML file using yq and Bitwarden CLI
# Usage: export_secrets <YAML_FILE> [<YQ_PATH>] [<PREFIX>] [<OUTPUT_FILE>] [<CASING[upper:lower]>]
# Function to export secrets from a YAML file using yq and Bitwarden CLI
# Usage: export_secrets <YAML_FILE> [<YQ_PATH>] [<PREFIX>] [<OUTPUT_FILE>] [<CASING>]
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

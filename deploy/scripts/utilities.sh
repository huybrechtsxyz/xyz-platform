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
# Arguments:
#   1. YAML file path
#   2. yq path to secrets (default: .spec.secrets)
#   3. Prefix for environment variables (optional)
#   4. Output file for secrets (optional)
# Returns:
#   0 on success, 1 on failure
export_secrets() {
  local yaml_file="$1"
  local yq_path="${2:-.spec.secrets}"
  local prefix="${3}"
  local env_file="${4}"
  
  log INFO "[*] ...Starting export of secrets from $yaml_file at path '$yq_path'"

  if [[ -z "$yaml_file" || ! -f "$yaml_file" ]]; then
    log ERROR "[X] Missing or invalid YAML file: $yaml_file" >&2
    return 1
  fi

  export BWS_ACCESS_TOKEN="${BITWARDEN_TOKEN:?Missing BITWARDEN_TOKEN environment variable}"

  local count=$(yq e "$yq_path | length" "$yaml_file" 2>/dev/null)
  if [[ "$count" == "0" || "$count" == "null" ]]; then
    log WARN "[!] No secrets found at path '$yq_path' in $yaml_file" >&2
    return 0
  fi

  # If an output file is defined, prepare it
  if [[ -n "$output_file" ]]; then
    mkdir -p "$(dirname "$output_file")"
    > "$output_file"
  fi

  for ((i = 0; i < count; i++)); do
    local key source id value data
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
    export "SECRET_${key}=$value"

    if [[ -n "$output_file" ]]; then
      echo "SECRET_${key}=${value@Q}" >> "$output_file"
    fi

    log INFO "[+] ......Exported SECRET_${key}"
  done
  log INFO "[+] ...Export completed for $count secrets from $yaml_file"
}

#!/bin/bash
#===============================================================================
# Script Name   : validate-workspace.sh
# Description   : Validate the structure and consistency of a workspace JSON
# Usage         : ./validate-workspace.sh <workspace-file.json>
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
set -euo pipefail

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

WORKSPACE_FILE="${1:-}"
if [[ -z "$WORKSPACE_FILE" || ! -f "$WORKSPACE_FILE" ]]; then
  log ERROR "[X] Missing or invalid workspace file"
  exit 1
fi

# Define allowed types as a Bash regex
VALID_TYPES_REGEX="^(config|docs|data|logs|serve)$"

check_top_levels(){
  local required_keys=("api-version" "workspace")
  for key in "${required_keys[@]}"; do
    if ! jq -e ".\"$key\"" "$WORKSPACE_FILE" > /dev/null; then
      log ERROR "[X] Missing required top-level key: $key"
      exit 1
    fi
  done

  # Check version is exactly 1.0
  local version
  version=$(jq -r '.["api-version"]' "$WORKSPACE_FILE")
  if [[ "$version" != "1.0" ]]; then
    log ERROR "[X] Unsupported api-version: '$version'. Only version 1.0 is allowed."
    exit 1
  fi

  # Check required top-level keys
  required_keys=("deploy" "roles" "paths" "variables" "secrets" "servers" "modules")
  for key in "${required_keys[@]}"; do
    if ! jq -e ".workspace.${key}" "$WORKSPACE_FILE" > /dev/null; then
      log ERROR "[X] Missing top-level key: $key"
      exit 1
    fi
  done
}

check_deploy_id(){
  # Check if deploy.id exists
  DEPLOY_ID=$(jq -r '.workspace.deploy.id // empty' "$WORKSPACE_FILE")
  if [[ -z "$DEPLOY_ID" ]]; then
    log ERROR "[X] 'workspace.deploy.id' is missing or empty in $WORKSPACE_FILE"
    exit 1
  fi
}

check_role_definition() {
  log INFO "[*] ...... Validating role definitions..."

  jq -c '.workspace.roles | to_entries[]' "$WORKSPACE_FILE" | while read -r role_entry; do
    role=$(jq -r '.key' <<< "$role_entry")
    cpu_cores=$(jq -r '.value.cpu_cores' <<< "$role_entry")
    ram_mb=$(jq -r '.value.ram_mb' <<< "$role_entry")
    unit_cost=$(jq -r '.value.unit_cost' <<< "$role_entry")

    # Validate CPU cores: integer >= 1
    if ! [[ "$cpu_cores" =~ ^[0-9]+$ ]] || (( cpu_cores < 1 )); then
      log ERROR "[X] Role '$role' has invalid cpu_cores: $cpu_cores"
      exit 1
    fi

    # Validate RAM: divisible by 1024
    if ! [[ "$ram_mb" =~ ^[0-9]+$ ]] || (( ram_mb % 1024 != 0 )); then
      log ERROR "[X] Role '$role' has ram_mb not divisible by 1024: $ram_mb"
      exit 1
    fi

    # Validate unit_cost: float or integer
    if ! [[ "$unit_cost" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      log ERROR "[X] Role '$role' has invalid unit_cost: $unit_cost"
      exit 1
    fi

    log INFO "[+] ...... Role '$role' is valid: cpu=$cpu_cores, ram=$ram_mb, cost=$unit_cost"
  done
}

check_path_types() {
  # Loop through each path type from the JSON
  jq -r '.workspace.paths[].type' "$WORKSPACE_FILE" | while read -r type; do
    if [[ ! "$type" =~ $VALID_TYPES_REGEX ]]; then
      log ERROR "[X] Invalid path type: $type"
      exit 1
    fi
  done

  log INFO "[+] ...... All path types are valid"
}

check_server_roles() {
  # Validate all server roles exist in .roles
  jq -r '.workspace.servers[].role' "$WORKSPACE_FILE" | while read -r role; do
    if ! jq -e --arg role "$role" '.workspace.roles[$role]' "$WORKSPACE_FILE" > /dev/null; then
      log ERROR "[X] Undefined role \"$role\" in servers"
      exit 1
    fi
  done
}

check_server_mounts() {
  local valid=1
  local VALID_TYPES_REGEX="^(data|logs|cache)$"  # Customize this to your valid mount types

  # Validate that all servers have a mountpoint defined
  jq -c '.workspace.servers[]' "$WORKSPACE_FILE" | while read -r server; do
    id=$(echo "$server" | jq -r '.id // .name')
    mp=$(echo "$server" | jq -r '.mountpoint // empty')

    if [[ -z "$mp" ]]; then
      log ERROR "[X] Missing mountpoint on server \"$id\""
      valid=0
    fi

    echo "$server" | jq -c '.mounts[]' | while read -r mount; do
      type=$(echo "$mount" | jq -r '.type // empty')
      if [[ ! "$type" =~ $VALID_TYPES_REGEX ]]; then
        log ERROR "[X] Invalid mount type \"$type\" on server \"$id\""
        valid=0
      fi
    done
  done

  if [[ "$valid" -eq 0 ]]; then
    return 1
  fi
}

check_server_disks() {
  # Validate mount disks refer to defined disks
  jq -c '.workspace.servers[]' "$WORKSPACE_FILE" | while read -r server; do
    id=$(echo "$server" | jq -r '.id')
    disk_count=$(echo "$server" | jq '.disks | length')

    echo "$server" | jq -c '.mounts[]' | while read -r mount; do
      disk=$(echo "$mount" | jq -r '.disk')
      if (( disk < 1 || disk > disk_count )); then
        log ERROR "[X] Invalid disk index $disk in server \"$id\" (has $disk_count disk(s))"
        exit 1
      fi
    done
  done
}

main() {
  log INFO "[*] ... Validating workspace definition..."
  check_top_levels
  check_deploy_id
  check_role_definition
  check_path_types
  check_server_roles
  check_server_mounts
  check_server_disks
  log INFO "[+] ... Workspace definition is valid."
}

main "$@"

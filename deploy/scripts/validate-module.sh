#!/bin/bash
#===============================================================================
# Script Name   : validate-module.sh
# Description   : Validate the structure and content of a service module JSON
# Usage         : ./validate-module.sh <module-file.json>
# Author        : Vincent Huybrechts
# Created       : 2025-07-25
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

MODULE_FILE="${1:-}"
if [[ -z "$MODULE_FILE" || ! -f "$MODULE_FILE" ]]; then
  log ERROR "[X] Missing or invalid module file"
  exit 1
fi

check_top_keys() {
  log INFO "[*] ...... Validating top-level keys..."

  local required_keys=("api-version" "module")
  for key in "${required_keys[@]}"; do
    if ! jq -e ".\"$key\"" "$MODULE_FILE" > /dev/null; then
      log ERROR "[X] Missing required top-level key: $key"
      exit 1
    fi
  done

  # Check version is exactly 1.0
  local version
  version=$(jq -r '.["api-version"]' "$MODULE_FILE")
  if [[ "$version" != "1.0" ]]; then
    log ERROR "[X] Unsupported api-version: '$version'. Only version 1.0 is allowed."
    exit 1
  fi

  log INFO "[✓] ...... api-version is valid: $version"
}

check_module_structure() {
  log INFO "[*] ...... Validating service block..."

  local required_service_keys=("id" "repository" "reference" "config" "deploy" "state")
  for key in "${required_service_keys[@]}"; do
    if ! jq -e ".service.$key" "$MODULE_FILE" > /dev/null; then
      log ERROR "[X] Missing required service key: $key"
      exit 1
    fi
  done

  state=$(jq -r '.service.state' "$MODULE_FILE")
  if [[ ! "$state" =~ ^(enabled|disabled|removed)$ ]]; then
    log ERROR "[X] Invalid state value: '$state'. Must be one of: enabled, disabled, removed."
    exit 1
  fi

  log INFO "[+] ...... Service definition is valid: id=$(jq -r '.service.id' "$MODULE_FILE"), state=$state"
}

main() {
  log INFO "[*] ... Validating Service Module..."
  check_top_keys
  check_module_structure
  log INFO "[+] ... Service Module is valid."
}

main "$@"

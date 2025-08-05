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
  log INFO "[*] ...... Validating module block..."

  local required_module_keys=("id" "repository" "reference" "config" "deploy")
  for key in "${required_module_keys[@]}"; do
    if ! jq -e ".module.$key" "$MODULE_FILE" > /dev/null; then
      log ERROR "[X] Missing required module key: $key"
      exit 1
    fi
  done

  log INFO "[+] ...... Module definition is valid: id=$(jq -r '.module.id' "$MODULE_FILE")"
}

main() {
  log INFO "[*] ... Validating Service Module..."
  check_top_keys
  check_module_structure
  log INFO "[+] ... Service Module is valid."
}

main "$@"

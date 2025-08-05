#!/bin/bash
#===============================================================================
# Script Name   : validate-service.sh
# Description   : Validate the structure and content of a service definition JSON
# Usage         : ./validate-service.sh <service-file.json>
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

check_top_keys() {
  log INFO "[*] ...... Validating top-level keys..."

  local required_keys=("api-version" "service")
  for key in "${required_keys[@]}"; do
    if ! jq -e ".\"$key\"" "$SERVICE_FILE" > /dev/null; then
      log ERROR "[X] Missing required top-level key: $key"
      exit 1
    fi
  done

  local version
  version=$(jq -r '.["api-version"]' "$SERVICE_FILE")
  if [[ "$version" != "1.0" ]]; then
    log ERROR "[X] Unsupported api-version: '$version'. Only version 1.0 is allowed."
    exit 1
  fi

  log INFO "[✓] ...... api-version is valid: $version"
}

check_service_structure() {
  log INFO "[*] ...... Validating service block..."

  local required_service_keys=("id" "name" "description" "deploy")
  for key in "${required_service_keys[@]}"; do
    if ! jq -e ".service.$key" "$SERVICE_FILE" > /dev/null; then
      log ERROR "[X] Missing required service key: $key"
      exit 1
    fi
  done

  log INFO "[✓] ...... Service metadata is valid: id=$(jq -r '.service.id' "$SERVICE_FILE")"
}

check_deploy_block() {
  log INFO "[*] ...... Looking for deploy block with workspace='$WORKSPACE' and environment='$ENVIRONMENT'..."

  DEPLOY=$(jq -e --arg ws "$WORKSPACE" --arg env "$ENVIRONMENT" \
    '.service.deploy[] | select(.workspace == $ws and .environment == $env)' "$SERVICE_FILE" || true)

  if [[ -z "$DEPLOY" ]]; then
    log ERROR "[X] No deploy block found matching workspace='$WORKSPACE' and environment='$ENVIRONMENT'"
    exit 1
  fi

  log INFO "[✓] ...... Found matching deploy block"

  local required_deploy_keys=("variables" "secrets" "endpoints" "addresses" "checks")

  for key in "${required_deploy_keys[@]}"; do
    if ! jq -e --arg ws "$WORKSPACE" --arg env "$ENVIRONMENT" \
      ".service.deploy[] | select(.workspace == \$ws and .environment == \$env) | .$key" "$SERVICE_FILE" > /dev/null; then
      log ERROR "[X] Missing required deploy key: $key"
      exit 1
    fi
  done

  log INFO "[✓] ...... Deploy block is structurally valid"
}

main() {
  SERVICE_FILE="${1:-}"
  if [[ -z "$SERVICE_FILE" || ! -f "$SERVICE_FILE" ]]; then
    log ERROR "[X] Missing or invalid registry file"
    exit 1
  fi

  WORKSPACE="${2:-}"
  if [[ -z "$WORKSPACE" ]]; then
    log ERROR "[X] Missing workspace"
    exit 1
  fi

  ENVIRONMENT="${3:-}"
  if [[ -z "$ENVIRONMENT" ]]; then
    log ERROR "[X] Missing workspace"
    exit 1
  fi

  check_top_keys
  check_service_structure
  check_deploy_block

  log INFO "[*] ... Validation service definition is valid."
  log INFO "[+] ... Service definition is valid."
}

main "$@"

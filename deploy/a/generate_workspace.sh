#!/bin/bash
#===============================================================================
# Script Name   : generate_workspace.sh
# Description   : Generate workspace variables for XYZ Platform
#                 This script is used to set up the environment for the XYZ Platform
#                 by generating necessary workspace variables.
# Usage         : ./generate_workspace.sh <WORKSPACE_FILE> <OUTPUT_FILE>
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-05
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: `$BASH_COMMAND`"' ERR

if ! command -v yq &> /dev/null; then
  echo "[X] ERROR yq is required but not installed."
  exit 1
fi

# Validate workspace parameters and environment variables
WORKSPACE_NAME=${1:-}
WORKSPACE_FILE="$SCRIPT_DIR/../../${$2}"
OUTPUT_FILE="${2:-$SCRIPT_DIR/../terraform/workspace.tfvars}"

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

if [[ -z "$OUTPUT_FILE" ]]; then
  log ERROR "[X] Output file not specified" >&2
  exit 1
fi

# Get workspace data
WORKSPACE_DATA=$(get_workspace_data "$WORKSPACE_NAME" "$WORKSPACE_FILE")

# Extract region and country
kamatera_country=$(yq '.spec.providers[] | select(.name == "kamatera") | .properties.country' "$WORKSPACE_FILE")
kamatera_region=$(yq '.spec.providers[] | select(.name == "kamatera") | .properties.region' "$WORKSPACE_FILE")

echo "# Generated from workspace.yml" > "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"
echo "kamatera_country = \"$country\"" >> "$OUTPUT_FILE"
echo "kamatera_region  = \"$region\"" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"
echo "virtualmachines = {" >> "$OUTPUT_FILE"

resource_count=$(yq '.spec.resources | length' "$WORKSPACE_FILE")
for (( i=0; i<resource_count; i++ )); do
  # Extract resource details
  name=$(yq ".spec.resources[$i].properties.type" "$WORKSPACE_FILE")
  provider=$(yq ".spec.resources[$i].properties.provider" "$WORKSPACE_FILE")
  template=$(yq ".spec.resources[$i].properties.template" "$WORKSPACE_FILE")
  count=$(yq ".spec.resources[$i].properties.count" "$WORKSPACE_FILE")

  # Template file path
  template_file=$(yq ".spec.templates[] | select(.name == \"$template\") | .file" "$WORKSPACE_FILE")
  if [[ -z "$template_file" ]]; then
    log WARN "[!] Template file not found for resource: $resource_name" >&2
    continue
  fi

  # Extract from template file
  os_name=$(yq ".spec.properties.osName" "$template_file")
  os_code=$(yq ".spec.properties.osCode" "$template_file")
  cpu_type=$(yq ".spec.properties.cpuType" "$template_file")
  cpu_cores=$(yq ".spec.properties.cpuCores" "$template_file")
  ram_mb=$(yq ".spec.properties.ramMb" "$template_file")
  billing=$(yq ".spec.properties.billing" "$template_file")
  unit_cost=$(yq ".spec.properties.unitCost" "$template_file")
  disks=$(yq ".spec.disks[].size" "$template_file" | sed ':a;N;$!ba;s/\n/, /g')
  echo "  $name = {" >> "$OUTPUT_FILE"
  echo "    provider  = \"$provider\"" >> "$OUTPUT_FILE"
  echo "    publickey = \"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC3...\"" >> "$OUTPUT_FILE"
  echo "    password  = \"securepassword123\"" >> "$OUTPUT_FILE"
  echo "    count     = $count" >> "$OUTPUT_FILE"
  echo "    os_name   = \"$os_name\"" >> "$OUTPUT_FILE"
  echo "    os_code   = \"$os_code\"" >> "$OUTPUT_FILE"
  echo "    cpu_type  = \"$cpu_type\"" >> "$OUTPUT_FILE"
  echo "    cpu_cores = $cpu_cores" >> "$OUTPUT_FILE"
  echo "    ram_mb    = $ram_mb" >> "$OUTPUT_FILE"
  echo "    disks_gb  = [$disks]" >> "$OUTPUT_FILE"
  echo "    billing   = \"$billing\"" >> "$OUTPUT_FILE"
  echo "    unit_cost = $unit_cost" >> "$OUTPUT_FILE"
  echo "  }," >> "$OUTPUT_FILE"
done

echo "}" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

log INFO "[+] Generated terraform workspace $OUTPUT_FILE"
cat "$OUTPUT_FILE"
log INFO "[+] Generated terraform workspace

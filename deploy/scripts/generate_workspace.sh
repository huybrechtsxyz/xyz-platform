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
WORKSPACE_NAME=$1
WORKSPACE_FILE=$2
OUTPUT_FILE=$3

: ${WORKSPACE_NAME:?"WORKSPACE_NAME is not set. Please set it to the workspace name."}
: ${WORKSPACE_FILE:?"WORKSPACE_FILE is not set. Please set it to the workspace file."}
: ${OUTPUT_FILE:?"OUTPUT_FILE is not set. Please set it to the workspace output file."}

# Validate workspace parameters and environment variables
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load utilities
source "$SCRIPT_DIR/utilities.sh"
load_script "$SCRIPT_DIR/use_workspace.sh"

# Get needed workspace data
log INFO "[*] ...For workspace: $WORKSPACE_NAME in $WORKSPACE_FILE"
WORKSPACE_DATA=$(get_ws_data "$WORKSPACE_NAME" "$WORKSPACE_FILE")
kamatera_country=$(yq '.spec.providers[] | select(.name == "kamatera") | .properties.country' "$WORKSPACE_FILE")
kamatera_region=$(yq '.spec.providers[] | select(.name == "kamatera") | .properties.region' "$WORKSPACE_FILE")

# Map declarations per resource type
vm_resources=()

# Build VM map in workspace
build_vm_entry() {
  local name="$1"
  local role="$2"
  local provider="$3"
  local count="$4"
  local template_file="$5"

  # Extract values from the template YAML file
  local os_name=$(yq -r '.spec.properties.osName' "$template_file")
  local os_code=$(yq -r '.spec.properties.osCode' "$template_file")
  local cpu_type=$(yq -r '.spec.properties.cpuType' "$template_file")
  local cpu_cores=$(yq -r '.spec.properties.cpuCores' "$template_file")
  local ram_mb=$(yq -r '.spec.properties.ramMb' "$template_file")
  local billing=$(yq -r '.spec.properties.billing' "$template_file")
  local unit_cost=$(yq -r '.spec.properties.unitCost' "$template_file")
  local disks=$(yq '.spec.disks[].size' "$template_file" | paste -sd ',' -)

  # Output the Terraform map entry as a multiline string
  cat << EOF
"$name" = {
  provider   = "$provider"
  role       = "$role"
  count      = $count
  os_name    = "$os_name"
  os_code    = "$os_code"
  cpu_type   = "$cpu_type"
  cpu_cores  = $cpu_cores
  ram_mb     = $ram_mb
  disks_gb   = [$disks]
  billing    = "$billing"
  unit_cost  = $unit_cost
},
EOF
}

# Export function for VM resources as Terraform map
export_virtualmachines() {
  # Export all collected VM entries at once, properly indented
  echo "virtualmachines = {" >> "$OUTPUT_FILE"
  for entry in "${vm_resources[@]}"; do
    # indent every line by two spaces
    printf '%s\n' "$entry" | sed 's/^/  /' >> "$OUTPUT_FILE"
  done
  echo "}" >> "$OUTPUT_FILE"
  echo "" >> "$OUTPUT_FILE"
}

# Main loop to get all the resources to create
resource_count=$(yq '.spec.resources | length' "$WORKSPACE_FILE")
for (( i=0; i<resource_count; i++ )); do

  name=$(yq -r ".spec.resources[$i].name" "$WORKSPACE_FILE")
  role=$(yq -r ".spec.resources[$i].properties.role" "$WORKSPACE_FILE")
  provider=$(yq -r ".spec.resources[$i].properties.provider" "$WORKSPACE_FILE")
  template=$(yq -r ".spec.resources[$i].properties.template" "$WORKSPACE_FILE")
  count=$(yq -r ".spec.resources[$i].properties.count" "$WORKSPACE_FILE")

  template_path=$(yq -r ".spec.templates[] | select(.name == \"$template\") | .file" "$WORKSPACE_FILE")
  template_file="$SCRIPT_DIR/../../$template_path"
  if [[ ! -f "$template_file" ]]; then
    log WARN "[!] Template file not found for resource: $name"
    continue
  fi

  kind=$(yq -r '.kind' "$template_file")
  case "$kind" in
    # Append the built VM entry (multi-line string) to the array
    VirtualMachine)
      vm_resources+=("$(build_vm_entry "$name" "$role" "$provider" "$count" "$template_file")")
      ;;
    # other kinds ...
  esac
done

# Export to workspace.tfvars
echo "# Generated from $WORKSPACE_FILE" > "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"
echo "kamatera_country = \"$kamatera_country\"" >> "$OUTPUT_FILE"
echo "kamatera_region  = \"$kamatera_region\"" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# Export all resource maps:
export_virtualmachines vm_resources

# Done generating
echo "# Generation complete" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

log INFO "[+] ...Generated terraform workspace $OUTPUT_FILE"
echo ==========================================
cat "$OUTPUT_FILE"
echo ==========================================
log INFO "[+] ...Generated terraform workspace"

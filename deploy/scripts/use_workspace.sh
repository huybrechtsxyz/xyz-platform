#!/bin/bash
#===============================================================================
# Script Name   : use_workspace.sh
# Description   : Use workspace variables for XYZ Platform
# Usage         : n.a. source this file in your shell
# Example       : Source deploy/scripts/use_workspace.sh
#                 Assumption is that uttilities.sh is already sourced
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-05
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: `$BASH_COMMAND`"' ERR

# Function to get workspace data from a YAML file
# Usage: get_ws_data <WORKSPACE_NAME> <WORKSPACE_FILE>
# Example: get_ws_data "my_workspace" "workspaces/workspace.yml"
get_ws_data() {
  local search_name="$1"
  local file="$2"

  if [[ -z "$search_name" || -z "$file" ]]; then
    log ERROR "[X] Usage: get_ws_data <WORKSPACE_NAME> <WORKSPACE_FILE>" >&2
    return 1
  fi

  if [[ ! -f "$file" ]]; then
    log ERROR "[X] Workspace file not found: $file" >&2
    return 1
  fi

  # Convert YAML → JSON array of documents
  local json_array=$(yq -o=json '.' "$file")

  # Find matching workspace object
  local match=$(echo "$json_array" | jq --arg name "$search_name" '
    map(select((.kind == "Workspace") and (.meta.name == $name))) | .[0]
  ')

  # Check if match is null
  if [[ "$match" == "null" ]]; then
    log ERROR "[X] No workspace found with name '$search_name'" >&2
    return 1
  fi

  # Output the matched workspace JSON (or convert back to YAML if you prefer)
  echo "$match"
}


#===============================================================================

# Function that returns the resourcedata based on the resourcename
# Usage: get_ws_resx_from_name <RESOURCE_NAME> <WORKSPACE_DATA>
# Returns: The resource data or an error message
get_ws_resx_from_name() {
  local resource_name="$1"
  local workspace_data="$2"

  if [[ -z "$resource_name" || -z "$workspace_data" ]]; then
    log ERROR "[X] Usage: get_workspace_resource_from_resourceid <RESOURCE_NAME> <WORKSPACE_DATA>" >&2
    return 1
  fi

  local resource_data=$(
    DATA="$resource_name" \
    yq -r '.spec.resources[] | select(.resourceid == strenv(DATA))' <<< "$workspace_data"
  )

  if [[ -z "$resource_data" ]]; then
    log ERROR "[X] No resource found with resource ID '$resource_name' in workspace data."
    exit 1
  fi

  echo "$resource_data"
}

# Function to get the installpoint from the resource data
# Usage: get_ws_resx_installpoint <RESOURCE_DATA>
# Returns: The installpoint or an error message
get_ws_resx_installpoint() {
  local resource_data="$1"
  if [[ -z "$resource_data" ]]; then
    log ERROR "[X] Usage: get_workspace_installpoint <RESOURCE_DATA>" >&2
    return 1
  fi

  local installpoint=$(yq -r '.properties.installpoint' <<< "$resource_data")
  if [[ -z "$installpoint" ]]; then
    log ERROR "[X] No installpoint found in resource data" >&2
    return 1
  fi

  echo "$installpoint"
}

# Function to get the mountpoint from the resource data
# Usage: get_ws_resx_mountpoint <RESOURCE_DATA>
# Returns: The mountpoint or an error message
get_ws_resx_mountpoint() {
  local resource_data="$1"
  if [[ -z "$resource_data" ]]; then
    log ERROR "[X] Usage: get_workspace_mountpoint <RESOURCE_DATA>" >&2
    return 1
  fi

  local mountpoint=$(yq -r '.properties.mountpoint' <<< "$resource_data")
  if [[ -z "$mountpoint" ]]; then
    log ERROR "[X] No mountpoint found in resource data" >&2
    return 1
  fi

  echo "$mountpoint"
}

# Function to get the firewall from the resource data
# Usage: get_ws_resx_firewall <RESOURCE_DATA>
# Returns: The firewall or an error message
get_ws_resx_firewall() {
  local resource_data="$1"
  if [[ -z "$resource_data" ]]; then
    log ERROR "[X] Usage: get_workspace_firewall <RESOURCE_DATA>" >&2
    return 1
  fi

  local value=$(yq -r '.properties.firewall' <<< "$resource_data")
  if [[ -z "$value" ]]; then
    log ERROR "[X] No firewall found in resource data" >&2
    return 1
  fi

  echo "$value"
}

# Function to get the template from the resource data
# Usage: get_ws_resx_template <RESOURCE_DATA>
# Returns: The template or an error message
get_ws_resx_template() {
  local resource_data="$1"
  if [[ -z "$resource_data" ]]; then
    log ERROR "[X] Usage: get_workspace_template <RESOURCE_DATA>" >&2
    return 1
  fi

  local value=$(yq -r '.properties.template' <<< "$resource_data")
  if [[ -z "$value" ]]; then
    log ERROR "[X] No template found in resource data" >&2
    return 1
  fi

  echo "$value"
}

#===============================================================================

#  Function to get the template file for a resource
# Usage: get_ws_template_file <WORKSPACE_DATA> <RESOURCE_NAME>
get_ws_template_file() {
  local workspace_data="$1"
  local template_name="$2"

  # Get file path from templates
  local file=$(echo "$workspace_data" | yq -r --arg name "$template_name" \
    '.spec.templates[] | select(.name == $name) | .file')

  if [[ -z "$file" ]]; then
    log ERROR "[X] No template file found for: $template_name" >&2
    return 1
  fi

  echo "$file"
}

# Check kind and type
# Usage: validate_template_firewall_file 
validate_template_firewall_file() {
  local fw_file="$1"
  if [[ -z "$fw_file" ]]; then
    log ERROR "[X] Usage: check_firewall_file <FIREWALL_FILE>" >&2
    return 1
  fi

  local kind=$(yq -r '.kind' "$fw_file")
  local type=$(yq -r '.meta.type' "$fw_file")

  if [[ "$kind" != "resourceType" || "$type" != "firewallRules" ]]; then
    echo "[ERROR] Invalid firewall file: not a firewallRules resourceType" >&2
    return 1
  fi
  
  return 0
}

#===============================================================================

get_ws_vm_disks() {
  local template_file="$1"

  if [[ ! -f "$template_file" ]]; then
    log ERROR "[X] Template file not found: $template_file" >&2
    return 1
  fi

  yq '.spec.disks | length' "$template_file"
}

get_ws_vm_disk_label() {
  local template_file="$1"
  local disk_index="$2"

  if [[ ! -f "$template_file" ]]; then
    log ERROR "[X] Template file not found: $template_file" >&2
    return 1
  fi

  yq -r ".spec.disks[$disk_index].label" "$template_file"
}

get_ws_vm_disk_size() {
  local template_file="$1"
  local disk_index="$2"

  if [[ ! -f "$template_file" ]]; then
    log ERROR "[X] Template file not found: $template_file" >&2
    return 1
  fi

  yq -r ".spec.disks[$disk_index].size" "$template_file"
}

resolve_disk_label() {
  local label_template="$1"
  local disk_index="$2"
  local resx_data="$3"
  
  if [[ ! -f "$resx_data" ]]; then
    echo "[X] Resource data file not found: $resx_data" >&2
    return 1
  fi

  # Match exactly one ${...}
  if [[ "$label_template" =~ \$\{([a-zA-Z0-9_.-]+)\} ]]; then
    # e.g., resource.name
    local var_path="${BASH_REMATCH[1]}"  
    # Convert to yq path: "resource.name" -> ["resource"]["name"]
    local yq_path=$(awk -F. '{for(i=1;i<=NF;i++) printf "[\"%s\"]", $i}' <<< "$var_path")
    # Resolve the value from the resource_data YAML
    local value=$(yq -r "$yq_path" <<< "$resx_data")
    # Replace only the first match
    local resolved_label="${label_template/\$\{$var_path\}/$value}"
    echo "$resolved_label"
  else
    # No template to resolve, just return raw label
    echo "$label_template"
  fi
}

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
  local name="$1"
  local file="$2"

  if [[ -z "$name" || -z "$file" ]]; then
    log ERROR "[X] Usage: get_workspace_data <WORKSPACE_NAME> <WORKSPACE_FILE>" >&2
    return 1
  fi

  if [[ ! -f "$file" ]]; then
    log ERROR "[X] Workspace file not found: $file" >&2
    return 1
  fi

  local matches=$(yq eval-all \
    "select(.kind == \"Workspace\" and .metadata.name == \"$name\")" \
    "$file" | yq eval -o=json '.' - | jq -s '.')

  local count=$(jq length <<< "$matches")

  if (( count == 0 )); then
    log ERROR "[X]  No Workspace found with name '$name'" >&2
    return 1
  elif (( count > 1 )); then
    log ERROR "[X]  Multiple Workspaces found with name '$name'" >&2
    return 1
  fi

  echo "$(jq '.[0]' <<< "$matches")"
}

# Function to get the manager ID from workspace data
# Usage: get_workspace_primary_machine <WORKSPACE_DATA>
# Returns: The manager ID or an error message
get_ws_primary_machine() {
  local workspace_data="$1"
  if [[ -z "$workspace_data" ]]; then
    log ERROR "[X] Usage: get_workspace_managerid <WORKSPACE_DATA>" >&2
    return 1
  fi

  local name=$(yq -r 'spec.properties.primaryMachine' <<< "$workspace_data")
  if [[ -z "$name" ]]; then
    log ERROR "[X] No primary machine found in workspace data" >&2
    return 1
  fi

  echo "$name"
}

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

  local resource_data=$(yq -r --arg resxname "$resource_name" '
    .spec.resources[] | select(.resourceid == $resxname)
  ' <<< "$workspace_data")

  if [[ -z "$resource_data" ]]; then
    log ERROR "[X] No resource found with resource ID '$RESOURCE_ID' in workspace data."
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































# Function to get the template base paths from workspace data
# Usage: get_workspace_template_basepaths <WORKSPACE_FILE>
get_workspace_template_uniquepaths() {
  local workspace_file="$1"
  yq -r '.spec.templates[].file' "$workspace_file" | \
    sed 's|/[^/]*$||' | \
    cut -d'/' -f1 | \
    sort -u
}

# Function to get the template file for a resource
# Usage: get_workspace_resource_template_file <WORKSPACE_DATA> <RESOURCE_NAME>
get_workspace_resource_template_file() {
  local workspace_data="$1"
  local resource_name="$2"
  if [[ -z "$workspace_data" ]]; then
    log ERROR "[X] Usage: get_workspace_managerid <WORKSPACE_DATA>" >&2
    return 1
  fi

  # Get firewall name from resource
  local firewall_name=$(echo "$workspace_data" | yq -r --arg rid "$resource_name" \
    '.spec.resources[] | select(.name == $rid) | .properties.firewall')

  if [[ -z "$firewall_name" ]]; then
    log ERROR "[X] No firewall property found for resource: $resource_id" >&2
    return 1
  fi

  # Get file path from templates
  local fw_file=$(echo "$workspace_data" | yq -r --arg name "$firewall_name" \
    '.spec.templates[] | select(.name == $name) | .file')

  if [[ -z "$fw_file" ]]; then
    log ERROR "[X] No template file found for firewall: $firewall_name" >&2
    return 1
  fi

  echo "$fw_file"
}





get_workspace_resource_() {
  echo "$1" | jq -r '.type'
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

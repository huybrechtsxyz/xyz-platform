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
# Usage: get_workspace_data <WORKSPACE_NAME> <WORKSPACE_FILE>
# Example: get_workspace_data "my_workspace" "workspaces/workspace.yml"
get_workspace_data() {
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
# Usage: get_workspace_managerid <WORKSPACE_DATA>
# Returns: The manager ID or an error message
get_workspace_managerid() {
  local workspace_data="$1"
  if [[ -z "$workspace_data" ]]; then
    log ERROR "[X] Usage: get_workspace_managerid <WORKSPACE_DATA>" >&2
    return 1
  fi

  local manager_name=$(yq -r 'spec.properties.primaryMachine' <<< "$workspace_data")
  if [[ -z "$manager_name" ]]; then
    log ERROR "[X] No manager found in workspace data" >&2
    return 1
  fi

  echo "$manager_name"
}

# Function thag returns the resource based on the hostname
get_workspace_resourceid_from_hostname() {
  local workspace_data="$1"
  local hostname="$2"

  if [[ -z "$workspace_data" || -z "$hostname" ]]; then
    log ERROR "[X] Usage: get_workspace_resourceid <WORKSPACE_DATA> <HOSTNAME>" >&2
    return 1
  fi

  local resource_id=$(yq -r ".spec.resources[] | select(.hostname == \"$hostname\") | .id" <<< "$workspace_data")
  
  if [[ -z "$resource_id" ]]; then
    log ERROR "[X] No resource found with hostname '$hostname'" >&2
    return 1
  fi

  echo "$resource_id"

}
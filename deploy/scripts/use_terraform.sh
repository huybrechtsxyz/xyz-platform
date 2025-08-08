#!/bin/bash
#===============================================================================
# Script Name   : use_terraform.sh
# Description   : Use terraform variables for XYZ Platform
# Usage         : n.a. source this file in your shell
# Example       : Source deploy/scripts/use_workspace.sh
#                 Assumption is that uttilities.sh is already sourced
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-08
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: `$BASH_COMMAND`"' ERR

# Get the terraform data as variable
get_tf_data() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    log ERROR "[X] terraform file not found: $file" >&2
    return 1
  fi
  cat "$file"
}

# Get the correct virtual machine from terraform output
get_tf_server_by_name() {
  local data="$1"
  local hostname="${2:-$(hostname)}"

  # Extract matching elements
  local matches=$(echo "$data" | jq -c --arg hostname "$hostname" '
    .virtualmachines[] | select(.name | contains($hostname))
  ')

  # Count matches
  local count=$(echo "$matches" | jq -s 'length')

  if [[ "$count" -eq 0 ]]; then
    log ERROR "[X] No matching server found for hostname: $hostname" >&2
    return 1
  elif [[ "$count" -gt 1 ]]; then
    log ERROR "[X] Multiple servers matched hostname: $hostname" >&2
    echo "$matches" | jq . >&2
    return 1
  fi

  # Return the single match
  echo "$matches"
}

#name":"srv-platform-infra-1-5jwb"
get_tf_vm_name() {
  echo "$1" | jq -r '.name'
}

#"resource":"vm-infrastructure"
get_tf_vm_resource() {
  echo "$1" | jq -r '.resource'
}

#"kind":"VirtualMachine"
get_tf_vm_kind() {
  echo "$1" | jq -r '.kind'
}

#"public_ip":"185.0.0.1"
get_tf_vm_publicip() {
  echo "$1" | jq -r '.public_ip'
}

#"private_ip":"10.0.0.1"
get_tf_vm_privateip() {
  echo "$1" | jq -r '.private_ip'
}

#"private_ip":"10.0.0.1"
get_tf_vm_managerip() {
  echo "$1" | jq -r '.manager_ip'
}

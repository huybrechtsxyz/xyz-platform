#!/bin/bash
#===============================================================================
# Script Name   : select-modules.sh
# Description   : Select Terraform modules defined in the workspace
# Usage         : ./select-modules.sh 'mod1,mod2,mod3' OR '*'
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-08-02
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: `$BASH_COMMAND`"' ERR

: "${WORKSPACE:?Environment variable WORKSPACE not set}"

# Setup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$SCRIPT_DIR/../../modules"
WORKSPACE_FILE="$SCRIPT_DIR/../../workspaces/${WORKSPACE}.ws.json"

# Source utilities
if [[ -f "$SCRIPT_DIR/utilities.sh" ]]; then
  source "$SCRIPT_DIR/utilities.sh"
  log INFO "[*] Loaded $SCRIPT_DIR/utilities.sh"
else
  echo "[X] Missing utilities.sh at $SCRIPT_DIR" >&2
  echo "selection=$(jq -c -n '{include: [{id: "", file: ""}]}')" >> "$GITHUB_OUTPUT"
  exit 1
fi

# Validate args
INPUT="${1:-*}"
log INFO "[*] Selecting modules: $INPUT"

# Validate workspace file
if [[ ! -f "$WORKSPACE_FILE" ]]; then
  log ERROR "[X] Workspace file not found: $WORKSPACE_FILE"
  echo "selection=$(jq -c -n '{include: [{id: "", file: ""}]}')" >> "$GITHUB_OUTPUT"
  exit 0
fi

# Get workspace module IDs
log INFO "[*] Reading module declarations from workspace"
workspace_modules=$(jq -r '.workspace.modules[]?.id' "$WORKSPACE_FILE" | sort -u || true)

if [[ -z "$workspace_modules" ]]; then
  log WARN "[!] No modules defined in workspace"
  echo "selection=$(jq -c -n '{include: [{id: "", file: ""}]}')" >> "$GITHUB_OUTPUT"
  exit 0
fi

# Determine selected module IDs
log INFO "[*] Resolving selection list"
if [[ "$INPUT" == "*" ]]; then
  selected_ids=($workspace_modules)
else
  IFS=',' read -ra selected_ids <<< "$INPUT"
fi

selected_set=$(printf '%s\n' "${selected_ids[@]}" | jq -R . | jq -s .)

# Find module files
log INFO "[*] Matching modules to files in $MODULE_DIR"
mapfile -t module_files < <(find "$MODULE_DIR" -name '*.json' 2>/dev/null)

if [[ "${#module_files[@]}" -eq 0 ]]; then
  log WARN "[!] No module files found in $MODULE_DIR"
  echo "selection=$(jq -c -n '{include: [{id: "", file: ""}]}')" >> "$GITHUB_OUTPUT"
  exit 0
fi

# Extract module matches
modules=$(jq -n --argjson ids "$selected_set" '
  [inputs
   | .include[]
   | {file, id}
   | select(.id != null and (.id | IN($ids[])))]
' "${module_files[@]}" 2>/dev/null || true)

if [[ -z "$modules" || "$modules" == "[]" ]]; then
  log WARN "[!] No matching modules found for selection"
  echo "selection=$(jq -c -n '{include: [{id: "", file: ""}]}')" >> "$GITHUB_OUTPUT"
  exit 0
fi

# Final output
log INFO "[*] Output selected modules"
echo "$modules" | jq .

log INFO "[*] Save selected modules for output"
echo "selection=$modules" >> $GITHUB_OUTPUT

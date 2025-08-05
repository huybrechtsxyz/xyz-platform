#!/bin/bash
#===============================================================================
# Script Name   : apply_terraform.sh
# Description   : Applies Terraform configurations using a specified workspace file.
#                 Terraform must be installed and configured.
#                 The script expects a workspace file as an argument.
# Usage         : ./apply_terraform.sh <WORKSPACE_FILE>
# Example       : ./apply_terraform.sh workspaces/workspace.yml
# Author        : Vincent Huybrechts
# Created       : 2025-08-05
# Last Modified : 2025-08-05
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: `$BASH_COMMAND`"' ERR

${VAR_PATH_TEMP:?"VAR_PATH_TEMP is not set. Please set it to the temporary variable path."}

# Generate the workspace.tfvars file base on the current workspace
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load utility functions
if [[ -f "$SCRIPT_DIR/utilities.sh" ]]; then
  source "$SCRIPT_DIR/utilities.sh"
  log INFO "[*] ...Loaded $SCRIPT_DIR/utilities.sh"
else
  log ERROR "[X] Missing utilities.sh at $SCRIPT_DIR"
  exit 1
fi

# Validate the WORKSPACE_NAME
WORKSPACE_NAME=${1:-}
if [[ -z "$WORKSPACE_NAME" ]]; then
  log ERROR "[X] No workspace name provided. Usage: $0 <WORKSPACE_NAME> <WORKSPACE_FILE>"
  exit 1
fi

# Validate the workspace file
WORKSPACE_FILE="$SCRIPT_DIR/../../${$2}"
if [[ ! -f "$WORKSPACE_FILE" ]]; then
  log ERROR "[X] Workspace file $WORKSPACE_FILE does not exist."
  exit 1
fi

# Check if workspace argument is provided
WORKSPACE=$(yq -r '.workspace.name' "$WORKSPACE_FILE")
if [[ -z "$WORKSPACE" ]]; then
  log ERROR "[X] No workspace file provided. Usage: $0 <WORKSPACE_NAME> <WORKSPACE_FILE>"
  exit 1
fi

# Validate the workspace name
if [[ "$WORKSPACE" != "$WORKSPACE_NAME" ]]; then
  log ERROR "[X] Workspace name mismatch. Expected: $WORKSPACE, Provided: $WORKSPACE_NAME"
  exit 1
fi

log INFO "[*] ...Validating workspace definition $WORKSPACE_FILE"
# TO DO: Implement validate_workspace function in utilities.sh
# validate_workspace "$WORKSPACE_FILE"

# Export secrets from the workspace file
log INFO "[*] ...Exporting secrets from $WORKSPACE_FILE"
export_secrets "$WORKSPACE_FILE" ".spec.secrets" "" ""

# Export necessary environment variables
log INFO "[*] ...Exporting environment variables for Terraform"
export TF_VAR_workspace="$WORKSPACE"
export TF_VAR_api_key="${KAMATERA_API_KEY:-}"
export TF_VAR_api_secret="${KAMATERA_API_SECRET:-}"
export TF_VAR_ssh_public_key="${KAMATERA_PUBLIC_KEY:-}"
export TF_VAR_password="${KAMATERA_ROOT_PASSWORD:-}"
export TF_VAR_bitwarden_token="${BITWARDEN_TOKEN:-}"

# Generate the workspace file
OUTPUT_FILE="$SCRIPT_DIR/../terraform/workspace.tfvars"
log INFO "[*] ...Generating $OUTPUT_FILE from $WORKSPACE_FILE"
chmod +x "$SCRIPT_DIR/generate_workspace.sh"
"$SCRIPT_DIR/generate_workspace.sh" "$WORKSPACE_FILE" "$OUTPUT_FILE"
log INFO "[*] ...Generated $OUTPUT_FILE successfully"

# Substitute environment variables in the main.template.tf file
export WORKSPACE=$WORKSPACE
cd "$SCRIPT_DIR/../terraform"
log INFO "[*] ...Generating main.tf from template"
envsubst < main.template.tf > main.tf
rm -f main.template.tf
cat main.tf

# Reason we do not save the plan
# Error: Saving a generated plan is currently not supported
# Terraform Cloud does not support saving the generated execution plan
log INFO "[*] ...Running terraform...INIT"
mkdir -p "$VAR_PATH_TEMP"
terraform init

log INFO "[*] ...Running terraform...PLAN"
terraform plan -var-file="workspace.tfvars" -input=false

log INFO "[*] ...Running terraform...APPLY"
#terraform apply -auto-approve -var-file="workspace.tfvars" -input=false
echo "[*] ...Running terraform...APPLY skipped for safety"

log INFO "[*] ...Reading Terraform output..."
terraform output -json serverdata | jq -c '.' | tee $VAR_PATH_TEMP/tf_output.json

log INFO "[+] ...Terraform output saved to tf_output.json and $VAR_PATH_TEMP/tf_output.json"

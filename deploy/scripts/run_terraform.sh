#!/bin/bash
#===============================================================================
# Script Name   : run_terraform.sh
# Description   : Applies Terraform configurations using a specified workspace file.
#                 Terraform must be installed and configured.
#                 The script expects a workspace file as an argument.
# Usage         : ./run_terraform.sh <WORKSPACE_FILE>
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
: ${VAR_WORKSPACE_NAME:?"VAR_WORKSPACE_NAME is not set. Please set it to the workspace name."}
: ${VAR_WORKSPACE_FILE:?"VAR_WORKSPACE_FILE is not set. Please set it to the workspace file."}
: ${VAR_PATH_INSTALL:?"VAR_PATH_INSTALL is not set. Please set it to the installation path."}
: ${VAR_PATH_TEMP:?"VAR_PATH_TEMP is not set. Please set it to the temporary variable path."}

# Variable assignments
WORKSPACE_FILE="$SCRIPT_DIR/../../${$2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load script utilities
load_script "$SCRIPT_DIR/utilities.sh"
load_script "$SCRIPT_DIR/use_workspace.sh"

# Get workspace data
WORKSPACE_DATA=$(get_workspace_data "$WORKSPACE_NAME" "$WORKSPACE_FILE")

# Primary machine label for the workspace
# ID of the manager VM, used for control and management
MANAGER_ID=$(get_workspace_managerid "$WORKSPACE_DATA")

log INFO "[*] ...Validating workspace definition $WORKSPACE_FILE"
# TO DO: Implement validate_workspace function in utilities.sh
# validate_workspace "$WORKSPACE_DATA"

# Export secrets from the workspace file
log INFO "[*] ...Exporting environment variables for Terraform"
export TF_VAR_workspace="$WORKSPACE_NAME"
export TF_VAR_manager_id="$MANAGER_ID"

log INFO "[*] ...Exporting secrets from $WORKSPACE_FILE"
export_secrets "$WORKSPACE_FILE" ".spec.secrets" "TF_VAR_" "" "lower"

# Generate the workspace file
OUTPUT_FILE="$SCRIPT_DIR/../terraform/workspace.tfvars"
log INFO "[*] ...Generating $OUTPUT_FILE from $WORKSPACE_FILE"
chmod +x "$SCRIPT_DIR/generate_workspace.sh"
"$SCRIPT_DIR/generate_workspace.sh" "$WORKSPACE_NAME" "$WORKSPACE_FILE" "$OUTPUT_FILE"
log INFO "[*] ...Generated $OUTPUT_FILE successfully"

# Substitute environment variables in the main.template.tf file
export WORKSPACE=$WORKSPACE_NAME
cd "$SCRIPT_DIR/../terraform"
log INFO "[*] ...Generating main.tf from template"
envsubst < main.template.tf > main.tf
rm -f main.template.tf
cat main.tf

# Reason we do not save the plan
# Error: Saving a generated plan is currently not supported
# Terraform Cloud does not support saving the generated execution plan
log INFO "[*] ...Running terraform...INIT"
mkdir -p "$VAR_PATH_INSTALL"
terraform init

log INFO "[*] ...Running terraform...PLAN"
terraform plan -var-file="workspace.tfvars" -input=false

log INFO "[*] ...Running terraform...APPLY"
#terraform apply -auto-approve -var-file="workspace.tfvars" -input=false
echo "[*] ...Running terraform...APPLY skipped for safety"

log INFO "[*] ...Reading Terraform output..."
terraform output -json serverdata | jq -c '.' | tee $VAR_PATH_INSTALL/$WORKSPACE_NAME.tfoutput.json

log INFO "[+] ...Terraform output saved to tf_output.json and $VAR_PATH_INSTALL/$WORKSPACE_NAME.tfoutput.json"

#!/bin/bash
#===============================================================================
# Script Name   : run_terraform.sh
# Description   : Applies Terraform configurations using a specified workspace file.
#                 Terraform must be installed and configured.
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

# Variable assignments
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Validate workspace parameters and environment variables
: ${WORKSPACE_NAME:?"WORKSPACE_NAME is not set. Please set it to the workspace name."}
: ${WORKSPACE_FILE:?"WORKSPACE_FILE is not set. Please set it to the workspace file."}
: ${PATH_TEMP:?"PATH_TEMP is not set. Please set it to the temporary path."}
: ${BITWARDEN_TOKEN?"BITWARDEN_TOKEN is not set. Please set it to the bitwarden api token."}

# Load script utilities
source "$SCRIPT_DIR/utilities.sh"
load_script "$SCRIPT_DIR/use_terraform.sh"
load_script "$SCRIPT_DIR/use_workspace.sh"

# Get workspace data
log INFO "[*] ...Workspace $WORKSPACE_NAME with $WORKSPACE_FILE"
WORKSPACE_FILE="$SCRIPT_DIR/../../$WORKSPACE_FILE"

log INFO "[*] ...Validating workspace definition $WORKSPACE_FILE for $WORKSPACE_NAME"
# TO DO: Implement validate_workspace function in utilities.sh
# validate_workspace "$WORKSPACE_FILE"

# Generate the workspace file
OUTPUT_FILE="./workspace.tfvars"
log INFO "[*] ...Generating workspace variables $OUTPUT_FILE"
chmod +x "$SCRIPT_DIR/generate_workspace.sh"
"$SCRIPT_DIR/generate_workspace.sh" "$WORKSPACE_NAME" "$WORKSPACE_FILE" "$OUTPUT_FILE"
log INFO "[*] ...Generation complete"

# Export secrets from the workspace file
log INFO "[*] ...Exporting fixed environment variables for Terraform"
export_variables "$WORKSPACE_FILE" ".spec.variables" "TF_VAR_" "" "lower"

log INFO "[*] ...Exporting secrets from $WORKSPACE_FILE"
export_secrets "$WORKSPACE_FILE" ".spec.secrets" "TF_VAR_" "" "lower"

log INFO "[*] ...Exporting fixed environment variables for Terraform"
export TF_TOKEN_app_terraform_io=$TF_VAR_terraform_api_token
export TF_VAR_workspace="$WORKSPACE_NAME"

log INFO "[*] ...Generating main.tf from template"
envsubst < main.template.tf > main.tf
rm -f main.template.tf
cat main.tf
log INFO "[*] ...Generation of main.tf complete"

# Reason we do not save the plan
# Error: Saving a generated plan is currently not supported
# Terraform Cloud does not support saving the generated execution plan
log INFO "[*] ...Running terraform...INIT"
mkdir -p "$PATH_TEMP"
terraform init

log INFO "[*] ...Running terraform...PLAN"
terraform plan -var-file="workspace.tfvars" -input=false

log INFO "[*] ...Running terraform...APPLY"
terraform apply -auto-approve -var-file="workspace.tfvars" -input=false
log INFO "[*] ...Running terraform...DONE"

log INFO "[*] ...Reading Terraform output..."
terraform output -json terraform_output | jq -c '.' | tee $PATH_TEMP/tfoutput.json

log INFO "[+] ...Terraform output saved to tf_output.json and $PATH_TEMP/tfoutput.json"

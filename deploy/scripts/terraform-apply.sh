#!/bin/bash
#===============================================================================
# Script Name   : terraform-apply.sh
# Description   : Initialize, plan, and apply the terraform code
# Usage         : ./terraform-apply.sh
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

# Generate the workspace.tfvars file base on the current workspace
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/utilities.sh" ]]; then
  source "$SCRIPT_DIR/utilities.sh"
  log INFO "[*] ...Loaded $SCRIPT_DIR/utilities.sh"
else
  log ERROR "[X] Missing utilities.sh at $SCRIPT_DIR"
  exit 1
fi

log INFO "[*] ...Generating ${WORKSPACE}.tfvars file"
WORKSPACE_FILE="$SCRIPT_DIR/../workspaces/${WORKSPACE}.ws.json"
OUTPUT_FILE="$SCRIPT_DIR/../terraform/workspace.tfvars"

# Validate the workspace definition
log INFO "[*] ...Validating workspace definition $WORKSPACE_FILE"
validate_workspace "$SCRIPT_DIR" "$WORKSPACE_FILE"

# Extract unique roles
roles=$(jq -r '.workspace.servers[].role' "$WORKSPACE_FILE" | sort | uniq)
if [ -z "$roles" ]; then
  log ERROR "[!] No server roles found in $WORKSPACE_FILE"
  exit 1
fi

# For each role, count servers and extract hardware profile and disks
# This will generate a block for each role in the tfvars file
log INFO "[*] ...Processing server roles and generating tfvars"
echo "server_roles = {" > "$OUTPUT_FILE"
for role in $roles; do
  # Count servers of this role
  count=$(jq --arg role "$role" '[.workspace.servers[] | select(.role == $role)] | length' "$WORKSPACE_FILE")
  # Get disk sizes from the first server of this role
  disks=$(jq --arg role "$role" '[.workspace.servers[] | select(.role == $role)][0].disks | map(.size)' "$WORKSPACE_FILE")
  # Get hardware profile for the role
  cpu_type=$(jq -r --arg role "$role" '.workspace.roles[$role].cpu_type' "$WORKSPACE_FILE")
  cpu_cores=$(jq -r --arg role "$role" '.workspace.roles[$role].cpu_cores' "$WORKSPACE_FILE")
  ram_mb=$(jq -r --arg role "$role" '.workspace.roles[$role].ram_mb' "$WORKSPACE_FILE")
  unit_cost=$(jq -r --arg role "$role" '.workspace.roles[$role].unit_cost' "$WORKSPACE_FILE")
  # Write block to tfvars
  echo "  $role = {" >> "$OUTPUT_FILE"
  echo "    count     = $count" >> "$OUTPUT_FILE"
  echo "    cpu_type  = \"$cpu_type\"" >> "$OUTPUT_FILE"
  echo "    cpu_cores = $cpu_cores" >> "$OUTPUT_FILE"
  echo "    ram_mb    = $ram_mb" >> "$OUTPUT_FILE"
  echo "    disks_gb  = $disks" >> "$OUTPUT_FILE"
  echo "    unit_cost = $unit_cost" >> "$OUTPUT_FILE"
  echo "  }" >> "$OUTPUT_FILE"
  log INFO "[+] Processed role: $role with $count servers"
done
echo "}" >> "$OUTPUT_FILE"
log INFO "[+] Terraform tfvars file generated at $OUTPUT_FILE"
log INFO "[+] Terraform tfvars file workspace.tfvars content:"
cat "$OUTPUT_FILE"
log INFO "[*] ...Generating workspace.tfvars file completed"

# Substitute environment variables in the main.template.tf file
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
terraform apply -auto-approve -var-file="workspace.tfvars" -input=false
#echo "[*] ...Running terraform...APPLY skipped for safety"

log INFO "[*] ...Reading Terraform output..."
terraform output -json serverdata | jq -c '.' | tee $VAR_PATH_TEMP/tf_output.json

log INFO "[+] ...Terraform output saved to tf_output.json and $VAR_PATH_TEMP/tf_output.json"

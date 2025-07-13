#!/bin/bash
set -euo pipefail
#cd "$(dirname "$0")/../deploy/terraform"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Generate the workspace.tfvars file base on the current workspace
echo "[*] ...Generating ${WORKSPACE}.tfvars file"
SERVERS_JSON="$SCRIPT_DIR/../workspaces/${WORKSPACE}.ws.json"
OUTPUT_FILE="$SCRIPT_DIR/../terraform/workspace.tfvars"

# Extract unique roles
roles=$(jq -r '.servers[].role' "$SERVERS_JSON" | sort | uniq)
if [ -z "$roles" ]; then
  echo "[!] No server roles found in $SERVERS_JSON"
  exit 1
fi

# For each role, count servers and extract hardware profile and disks
# This will generate a block for each role in the tfvars file
echo "[*] ...Processing server roles and generating tfvars"
echo "server_roles = {" > "$OUTPUT_FILE"
for role in $roles; do
  # Count servers of this role
  count=$(jq --arg role "$role" '[.servers[] | select(.role == $role)] | length' "$SERVERS_JSON")
  # Get disk sizes from the first server of this role
  disks=$(jq --arg role "$role" '[.servers[] | select(.role == $role)][0].disks | map(.size)' "$SERVERS_JSON")
  # Get hardware profile for the role
  cpu_type=$(jq -r --arg role "$role" '.roles[$role].cpu_type' "$SERVERS_JSON")
  cpu_cores=$(jq -r --arg role "$role" '.roles[$role].cpu_cores' "$SERVERS_JSON")
  ram_mb=$(jq -r --arg role "$role" '.roles[$role].ram_mb' "$SERVERS_JSON")
  unit_cost=$(jq -r --arg role "$role" '.roles[$role].unit_cost' "$SERVERS_JSON")
  # Write block to tfvars
  echo "  $role = {" >> "$OUTPUT_FILE"
  echo "    count     = $count" >> "$OUTPUT_FILE"
  echo "    cpu_type  = \"$cpu_type\"" >> "$OUTPUT_FILE"
  echo "    cpu_cores = $cpu_cores" >> "$OUTPUT_FILE"
  echo "    ram_mb    = $ram_mb" >> "$OUTPUT_FILE"
  echo "    disks_gb  = $disks" >> "$OUTPUT_FILE"
  echo "    unit_cost = $unit_cost" >> "$OUTPUT_FILE"
  echo "  }" >> "$OUTPUT_FILE"
  echo "[+] Processed role: $role with $count servers"
done
echo "}" >> "$OUTPUT_FILE"
echo "[+] Terraform tfvars file generated at $OUTPUT_FILE"
echo "[+] Terraform tfvars file workspace.tfvars content:"
cat "$OUTPUT_FILE"
echo "[*] ...Generating workspace.tfvars file completed"

# Substitute environment variables in the main.template.tf file
cd "$SCRIPT_DIR/../terraform"
echo "[*] ...Generating main.tf from template"
envsubst < main.template.tf > main.tf
rm -f main.template.tf
cat main.tf

# Reason we do not save the plan
# Error: Saving a generated plan is currently not supported
# Terraform Cloud does not support saving the generated execution plan

echo "[*] ...Running terraform...INIT"
mkdir -p "$APP_PATH_TEMP"
terraform init

echo "[*] ...Running terraform...PLAN"
terraform plan -var-file="workspace.tfvars" -input=false

echo "[*] ...Running terraform...APPLY"
#terraform apply -auto-approve -var-file="workspace.tfvars" -input=false
echo "[*] ...Running terraform...APPLY skipped for safety"

echo "[*] ...Reading Terraform output..."
terraform output -json serverdata | jq -c '.' | tee $APP_PATH_TEMP/tf_output.json

echo "[*] ...Terraform output saved to tf_output.json and $APP_PATH_TEMP/tf_output.json"

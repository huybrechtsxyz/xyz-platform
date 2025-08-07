# Output the server details
# {
#   "virtualmachines": [
#     {
#       "name": "srv-platform-infra-1-random1234",
#       "resourceid": "vm-infrastructure"
#       "kind": "virtualmachine"
#       "type": "infra",
#
#       "index": "1",
#       "label": "infra-1",
#
#       "public_ip": "123...",
#       "private_ip": "10.0.0.4",
#       "manager_ip": "10.0.0.1",
#     }
#   ]
# }
# Name example: srv-shared-worker-3-1234

# Terraform Output Configuration
output "terraform_output" {
  value = {
    # Virtual machines
    virtualmachines = concat(
      # Get the kamatera virtual machines
      module.kamatera_vms.kamatera_output.virtualmachines
    )
  }
}

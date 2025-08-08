# Output the server details
# {
#   "virtualmachines": [
#     {
#       "index": 1,
#       "kind": "VirtualMachine",
#       "label": "infra-1",
#       "manager_ip": "10.0.0.4",
#       "name": "srv-platform-infra-1-5jwb",
#       "private_ip": "10.0.0.3",
#       "public_ip": "185.0.0.1",
#       "resource": "vm-infrastructure",
#       "role": "infra"
#     },
#     ...
#   ]
# }
# Name example: srv-shared-worker-3-1234

# Terraform Output Configuration
output "terraform_output" {
  value = {
    # Virtual machines
    virtualmachines = concat(
      # Get the kamatera virtual machines
      values(module.kamatera_vms.kamatera_output.virtualmachines)
    )
  }
}

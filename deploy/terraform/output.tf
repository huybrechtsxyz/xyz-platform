# Output the server details
# {
#   "include": [
#     {
#       "index": "1",
#       "ip": "123...",
#       "label": "infra-1",
#       "manager_ip": "10.0.0.1",
#       "name": "srv-platform-infra-1-random1234",
#       "private_ip": "10.0.0.4",
#       "type": "infra"
#     }
#   ]
# }
# Name example: srv-shared-worker-3-1234

# Terraform Output Configuration
output "serverdata" {
  value = {
    include = concat(
      module.kamatera_vms.kamatera_serverdata.include
    )
  }
}

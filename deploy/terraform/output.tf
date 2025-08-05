# Terraform Output Configuration
output "serverdata" {
  value = {
    include = concat(
      module.kamatera_vms.kamatera_serverdata.include
    )
  }
}

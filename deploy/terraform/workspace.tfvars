# Example file that will be overwritten by the pipeline in terraform apply
# Environment configuration file for Terraform
# Server roles configuration
server_roles = {
  manager = {
    count     = 1
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 1024
    disks_gb  = [20]  # root only
    unit_cost = 5.00
  }
  infra = {
    count     = 2
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 4096
    disks_gb  = [20,40]  # root + block
    unit_cost = 11.00
  }
  worker = {
    count     = 2
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 2048
    disks_gb  = [20]
    unit_cost = 6.00
  }
}

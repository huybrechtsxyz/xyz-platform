# Environment configuration file for Terraform
# Overwritten by the pipeline in terraform apply

# Kamatera Datacenter Cariables
kamatera_country = "Germany"
kamatera_region = "Frankfurt"

# Server configuration
virtualmachines = {
  "vm-manager" = {
    provider  = "kamatera"
    role      = "manager"
    count     = 1
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 1024
    disks_gb  = [20]  # root only
    billing   = "hourly"
    unit_cost = 5.00
  }
  "vm-infrastructure" = {
    provider  = "kamatera"
    role      = "infra"
    count     = 2
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 4096
    disks_gb  = [20,40]  # root + block
    billing   = "hourly"
    unit_cost = 11.00
  }
  "vm-workers" = {
    provider  = "kamatera"
    role      = "worker"
    count     = 2
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 2048
    disks_gb  = [20]
    billing   = "hourly"
    unit_cost = 6.00
  }
}

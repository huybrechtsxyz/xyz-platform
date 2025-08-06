# Environment configuration file for Terraform
# Overwritten by the pipeline in terraform apply

# Kamatera Datacenter Cariables
kamatera_country = "Germany"
kamatera_region = "Frankfurt"

# Server configuration
virtualmachines = {
  manager = {
    provider  = "kamatera"
    publickey = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC3..."
    password  = "securepassword123"
    count     = 1
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 1024
    disks_gb  = [20]  # root only
    billing   = "monthly"
    unit_cost = 5.00
  }
  infra = {
    provider  = "kamatera"
    publickey = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC3..."
    password  = "securepassword123"
    count     = 2
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 4096
    disks_gb  = [20,40]  # root + block
    billing   = "monthly"
    unit_cost = 11.00
  }
  worker = {
    provider  = "kamatera"
    publickey = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC3..."
    password  = "securepassword123"
    count     = 2
    os_name   = "Ubuntu"
    os_code   = "24.04 64bit"
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 2048
    disks_gb  = [20]
    billing   = "monthly"
    unit_cost = 6.00
  }
}

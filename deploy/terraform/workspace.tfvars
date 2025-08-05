# Environment configuration file for Terraform
# Overwritten by the pipeline in terraform apply

# Server roles configuration
virtualmachines = {
  manager = {
    provider  = "kamatera"
    publickey = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC3..."
    password  = "securepassword123"
    count     = 1
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 1024
    disks_gb  = [20]  # root only
    unit_cost = 5.00
  }
  infra = {
    provider  = "kamatera"
    publickey = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC3..."
    password  = "securepassword123"
    count     = 2
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 4096
    disks_gb  = [20,40]  # root + block
    unit_cost = 11.00
  }
  worker = {
    provider  = "kamatera"
    publickey = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC3..."
    password  = "securepassword123"
    count     = 2
    cpu_type  = "A"
    cpu_cores = 1
    ram_mb    = 2048
    disks_gb  = [20]
    unit_cost = 6.00
  }
}

# This file is part of the Huybrechts.xyz Terraform configuration.
terraform {
  required_providers {
    kamatera = {
      source = "Kamatera/kamatera"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
  
  cloud {
    organization = "huybrechts-xyz"
    workspaces {
      name = "xyz-$WORKSPACE"
    }
  }
}

# Local variables
locals {
  # Flatten the virtual machines configuration into a list of server objects.
  virtualservers = flatten([
    for role, cfg in var.virtualmachines :
    [
      for i in range(cfg.count) : {
        provider    = cfg.provider
        publickey   = cfg.publickey
        password    = cfg.password
        full_name   = "${role}-${i + 1}"
        role        = role
        cpu_cores   = cfg.cpu_cores
        cpu_type    = cfg.cpu_type
        ram_mb      = cfg.ram_mb
        disks_gb    = cfg.disks_gb
        unit_cost   = cfg.unit_cost
      }
    ]
  ])

  # Filter out only Kamatera VMs from the virtual servers
  kamatera_vms = {
    for role, cfg in local.virtualservers : role => cfg
    if cfg.provider == "kamatera"
  }
}

# Define the data center we will create the server and all related resources in
module "kamatera_vm" {
  source = "./modules/kamatera-vm"
  virtualmachines = local.kamatera_vms
  kamatera_api_key = var.kamatera_api_key
  kamatera_api_secret = var.kamatera_api_secret
}

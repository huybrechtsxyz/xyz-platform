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
    for type, cfg in var.virtualmachines :
    [
      for i in range(cfg.count) : {
        provider    = cfg.provider
        publickey   = cfg.publickey
        password    = cfg.password
        full_name   = "${type}-${i + 1}"
        type        = type
        os_name     = cfg.os_name
        os_code     = cfg.os_code
        cpu_cores   = cfg.cpu_cores
        cpu_type    = cfg.cpu_type
        ram_mb      = cfg.ram_mb
        disks_gb    = cfg.disks_gb
        billing     = cfg.billing
        unit_cost   = cfg.unit_cost
      }
    ]
  ])

  # Filter out only Kamatera VMs from the virtual servers
  kamatera_vms = {
    for type, cfg in local.virtualservers : type => cfg
    if cfg.provider == "kamatera"
  }
}

# Define the data center we will create the server and all related resources in
module "kamatera_vm" {
  source = "./modules/kamatera-vm"
  workspace = var.workspace
  manager_id = var.manager_id
  virtualmachines = local.kamatera_vms
  kamatera_api_key = var.kamatera_api_key
  kamatera_api_secret = var.kamatera_api_secret
  kamatera_country = var.kamatera_country
  kamatera_region = var.kamatera_region
}

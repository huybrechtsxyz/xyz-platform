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
      name = "huybrechts-xyz-$workspace"
    }
  }
}

# Local variables
locals {
  # Flatten the virtual machines configuration into a list of server objects.
  virtualservers = flatten([
    for key, cfg in var.virtualmachines :
    [
      for i in range(cfg.count) : {
        provider    = cfg.provider
        resource    = key
        role        = cfg.role
        label       = "${cfg.role}-${i + 1}"
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
  # Make it a map again wiht { fullname: {object} }
  kamatera_vms = {
    for vm in local.virtualservers : 
    vm.label => vm
    if vm.provider == "kamatera"
  }
}

# Define the data center we will create the server and all related resources in
module "kamatera_vms" {
  source = "./modules/kamatera_vms"
  workspace = var.workspace
  kamatera_root_password = var.kamatera_root_password
  kamatera_public_key = var.kamatera_public_key
  manager_id = var.manager_id
  kamatera_vms = local.kamatera_vms
  kamatera_api_key = var.kamatera_api_key
  kamatera_api_secret = var.kamatera_api_secret
  kamatera_country = var.kamatera_country
  kamatera_region = var.kamatera_region
}

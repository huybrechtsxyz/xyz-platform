terraform {
  required_providers {
    kamatera = {
      source = "Kamatera/kamatera"
    }
  }
}

# Provider configuration for Kamatera
provider "kamatera" {
  api_client_id = var.kamatera_api_key
  api_secret = var.kamatera_api_secret
}

# define the data center we will create the server and all related resources in
# see the section below "Listing available data centers" for more details
data "kamatera_datacenter" "dc1" {
  country = var.kamatera_country
  name  = var.kamatera_region
}

# define the server image we will create the server with
# see the section below "Listing available public images" for more details
# also see "Using a private image" if you want to use a private image you created yourself
data "kamatera_image" "images" {
  for_each = {
    for image in local.unique_images :
    "${image.os_name}-${image.os_code}" => image
  }

  datacenter_id = data.kamatera_datacenter.dc1.id
  os            = each.value.os_name
  code          = each.value.os_code
}

# Create a random suffix resource
resource "random_string" "suffix" {
  length  = 4
  upper   = false
  special = false
}

locals {
  unique_images = distinct([
    for s in var.kamatera_vms : {
      os_name = s.os_name
      os_code = s.os_code
    }
  ])
}

# Set up private network
# Name example: vlan-shared-1234
resource "kamatera_network" "private-lan" {
  datacenter_id = data.kamatera_datacenter.dc1.id
  name = "vlan-${var.workspace}-${random_string.suffix.result}"

  subnet {
    ip = "10.0.0.0"
    bit = 23
  }
}

# Provision servers: for each virtual machine on kamatera
# Name example: srv-shared-manager-1-1234
resource "kamatera_server" "server" {
  for_each = var.kamatera_vms

  name          = "srv-${var.workspace}-${each.key}-${random_string.suffix.result}"
  image_id      = data.kamatera_image.images["${each.value.os_name}-${each.value.os_code}"].id
  datacenter_id = data.kamatera_datacenter.dc1.id
  cpu_cores     = each.value.cpu_cores
  cpu_type      = each.value.cpu_type
  ram_mb        = each.value.ram_mb
  disk_sizes_gb = each.value.disks_gb
  billing_cycle = each.value.billing
  power_on      = true
  password      = var.kamatera_root_password
  ssh_pubkey    = var.kamatera_public_key

  network {
    name = "wan"
  }

  network {
    name = kamatera_network.private-lan.full_name
  }
}

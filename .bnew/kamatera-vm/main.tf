# This Terraform module provisions virtual machines on Kamatera.
# It uses the Kamatera provider to create servers and a private network.
# The module expects a variable `virtualmachines` which defines the roles and configurations for the servers to be created.

# Flatten the server roles configuration into a list of server objects.
locals {
  servers = flatten([
    for role, cfg in var.kamateravms :
    [
      for i in range(cfg.count) : {
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
}

# define the data center we will create the server and all related resources in
# see the section below "Listing available data centers" for more details
data "kamatera_datacenter" "frankfurt" {
  country = "Germany"
  name = "Frankfurt"
}

# define the server image we will create the server with
# see the section below "Listing available public images" for more details
# also see "Using a private image" if you want to use a private image you created yourself
data "kamatera_image" "ubuntu" {
  datacenter_id = data.kamatera_datacenter.frankfurt.id
  os = "Ubuntu"
  code = "24.04 64bit"
}

# Random string to append to server names for uniqueness.
resource "random_string" "suffix" {
  length  = 4
  upper   = false
  special = false
}

# Set up private network
# Name example: vlan-shared-1234
resource "kamatera_network" "private-lan" {
  datacenter_id = data.kamatera_datacenter.frankfurt.id
  name = "vlan-${var.workspace}-${random_string.suffix.result}"

  subnet {
    ip = "10.0.0.0"
    bit = 23
  }
}

# Provision servers
# Name example: srv-shared-manager-1-1234
# Name example: srv-shared-infra-2-1234
# Name example: srv-shared-worker-3-1234
resource "kamatera_server" "server" {
  for_each         = { for server in local.servers : server.full_name => server }

  name             = "srv-${var.workspace}-${each.value.full_name}-${random_string.suffix.result}"
  image_id         = data.kamatera_image.ubuntu.id
  datacenter_id    = data.kamatera_datacenter.frankfurt.id
  cpu_cores        = each.value.cpu_cores
  cpu_type         = each.value.cpu_type
  ram_mb           = each.value.ram_mb
  disk_sizes_gb    = each.value.disks_gb
  billing_cycle    = "hourly"
  power_on         = true
  password         = var.password
  ssh_pubkey       = var.ssh_public_key

  network {
    name = "wan"
  }

  network {
    name = kamatera_network.private-lan.full_name
  }
}

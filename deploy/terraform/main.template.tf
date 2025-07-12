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
      name = "huybrechts-xyz-$WORKSPACE"
    }
  } 
}

provider "kamatera" {
  api_client_id = var.api_key
  api_secret = var.api_secret
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

# Create a random suffix resource
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

#
# THESE VARIABLES ARE REQUIRED FOR THE DEPLOYMENT
#

# Workspace
variable "workspace" {
  description = "Workspace: test, staging, production"
  type        = string  
}

# Public key for SSH access to the servers
variable "kamatera_public_key" {
  description = "Public key for SSH access to the servers"
  type        = string
  sensitive   = true
}

# Password for the servers
variable "kamatera_root_password" {
  description = "Password for the servers"
  type        = string
  sensitive   = true
}

# Kamatera API details
variable "kamatera_api_key" {
  description = "Kamatera API key"
  type        = string
  sensitive   = true
}

variable "kamatera_api_secret" {
  description = "Kamatera API secret"
  type        = string
  sensitive   = true
}

#
# THESE VARIABLES ARE COVERED BY THE TERRAFORM VARIABLES FILE
#

# Primary machine label for the workspace
variable "manager_id" {
  description = "ID of the manager VM, used for control and management"
  type        = string
}

# Kamatera Datacenter Cariables
variable "kamatera_country" {
  description = "Kamatera Country"
  type        = string
}

variable "kamatera_region" {
  description = "Kamatera Region"
  type        = string
}

# Virtual Machines Configuration for Terraform
variable "virtualmachines" {
  type = map(object({
    provider   : string
    role       : string
    count      : number
    os_name    : string
    os_code    : string
    cpu_cores  : number
    cpu_type   : string
    ram_mb     : number
    disks_gb   : list(number)
    billing    : string
    unit_cost  : number
  }))
  description = "Map of servers and their hardware specs including list of disk sizes"

  validation {
    condition = alltrue([for r in var.virtualmachines : contains(["A", "B", "D", "T"], r.cpu_type) ])
    error_message = "Type of manager nodes must be one of A, B, D, or T."
  }

  validation {
    condition     = alltrue([for r in var.virtualmachines : r.ram_mb % 1024 == 0])
    error_message = "Each server's RAM must be a multiple of 1024 MB."
  }

  validation {
    condition = alltrue([for r in var.virtualmachines : contains(["hourly", "monthly"], r.billing) ])
    error_message = "Type of billig cycle must be one of hourly or monthly."
  }
}

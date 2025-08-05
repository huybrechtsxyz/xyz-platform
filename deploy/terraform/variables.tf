#
# THESE VARIABLES ARE REQUIRED FOR THE DEPLOYMENT
#

# Workspace
variable "workspace" {
  description = "Workspace: test, staging, production"
  type        = string  
}

# Kamatera API details
variable "kamatera_api_key" {
  description = "Kamatera API key"
  type        = string
}

variable "kamatera_api_secret" {
  description = "Kamatera API secret"
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

#
# THESE VARIABLES ARE COVERED BY THE TERRAFORM VARIABLES FILE
#

# Virtual Machines Configuration for Terraform
variable "virtualmachines" {
  type = map(object({
    provider  : string
    publickey : string
    password  : string
    count     : number
    os_name   : string
    os_code   : string
    cpu_cores : number
    cpu_type  : string
    ram_mb    : number
    disks_gb  : list(number)
    billing   : string
    unit_cost : number
  }))
  description = "Map of server roles and their hardware specs including list of disk sizes"

  validation {
    condition = alltrue([for r in var.virtualmachines : contains(["A", "B", "D", "T"], r.cpu_type) ])
    error_message = "Type of manager nodes must be one of A, B, D, or T."
  }

  validation {
    condition     = alltrue([for r in var.virtualmachines : r.ram_mb % 1024 == 0])
    error_message = "Each role's RAM must be a multiple of 1024 MB."
  }

  validation {
    condition = alltrue([for r in var.virtualmachines : contains(["hourly", "monthly"], r.billing) ])
    error_message = "Type of billig cycle must be one of hourly or monthly."
  }
}

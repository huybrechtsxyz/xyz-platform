#
# THESE VARIABLES ARE REQUIRED FOR THE DEPLOYMENT
#

# Kamatera API details
variable "api_key" {
  description = "Kamatera API key"
  type        = string
}

variable "api_secret" {
  description = "Kamatera API secret"
  type        = string
}

# SSH key for SSH access to the server
variable "ssh_public_key" {
  description = "The public SSH key to use for access"
  type        = string
}

variable "password" {
  description = "Root password for the servers"
  type        = string
}

#
# THESE VARIABLES ARE COVERED BY THE TERRAFORM VARIABLES FILE
#

# Workspace and Environment
variable "workspace" {
  description = "Workspace: test, staging, production"
  type        = string  
}

variable "environment" {
  description = "Environment to deploy: test, staging, production"
  type        = string
}

variable "server_roles" {
  type = map(object({
    count     : number
    cpu_cores : number
    cpu_type  : string
    ram_mb    : number
    disks_gb  : list(number)
    unit_cost : number
  }))
  description = "Map of server roles and their hardware specs including list of disk sizes"

  validation {
    condition = alltrue([for r in var.server_roles : contains(["A", "B", "D", "T"], r.cpu_type) ])
    error_message = "Type of manager nodes must be one of A, B, D, or T."
  }

  validation {
    condition     = alltrue([for r in var.server_roles : r.ram_mb % 1024 == 0])
    error_message = "Each role's RAM must be a multiple of 1024 MB."
  }
}

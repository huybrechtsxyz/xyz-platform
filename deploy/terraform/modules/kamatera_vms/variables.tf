# Kamatera API details
variable "kamatera_api_key" {
  description = "Kamatera API key"
  type        = string
}

variable "kamatera_api_secret" {
  description = "Kamatera API secret"
  type        = string
}

# Virtual Machines Configuration for Terraform
variable "virtualmachines" {
  type = map(object({
    provider  : string
    publickey : string
    password  : string
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

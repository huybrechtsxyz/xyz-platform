variable "workspace" {
  description = "Environment/workspace name"
  type        = string
}

variable "kamatera_servers" {
  type = map(object({
    count     : number
    cpu_cores : number
    cpu_type  : string
    ram_mb    : number
    disks_gb  : list(number)
    unit_cost : number
  }))
}

variable "api_key" {
  type = string
}

variable "api_secret" {
  type = string
}

variable "ssh_public_key" {
  type = string
}

variable "password" {
  type = string
}

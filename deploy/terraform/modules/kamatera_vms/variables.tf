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

# Primary machine label for the workspace
variable "manager_id" {
  description = "ID of the manager VM, used for control and management"
  type        = string
  default     = "manager-1"  # Default value, can be overridden in workspace
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

# KAMATERA: Virtual Machines Configuration for Terraform
variable "kamatera_vms" {
  type = map(object({
    provider   : string
    resource   : string
    role       : string
    label      : string
    os_name    : string
    os_code    : string
    cpu_cores  : number
    cpu_type   : string
    ram_mb     : number
    disks_gb   : list(number)
    billing    : string
    unit_cost  : number
  }))
}

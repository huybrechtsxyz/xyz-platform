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

# Primary machine label for the workspace
variable "manager_id" {
  description = "ID of the manager VM, used for control and management"
  type        = string
  default     = "manager-1"  # Default value, can be overridden in workspace
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

# Virtual Machines Configuration for Terraform
variable "virtualmachines" {
  type = map(object({
    provider   : string
    resourceid : string
    publickey  : string
    password   : string
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
}

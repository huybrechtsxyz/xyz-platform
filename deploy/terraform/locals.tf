locals {
  servers = flatten([
    for role, cfg in var.server_roles :
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

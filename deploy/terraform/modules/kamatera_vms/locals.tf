locals {
  unique_images = distinct([
    for s in var.virtualmachines : {
      os_name = s.os_name
      os_code = s.os_code
    }
  ])
}
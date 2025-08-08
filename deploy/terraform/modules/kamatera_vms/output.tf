# Kamatera Output : virtual machines
output "kamatera_output" {
  value = {
    virtualmachines = {
      for key, srv in kamatera_server.server :
      key => {
        kind       = "VirtualMachine"
        resource   = var.kamatera_vms[key].resource
        label      = "${split("-", key)[0]}-${split("-", key)[1]}"
        role       = split("-", key)[0]
        index      = tonumber(split("-", key)[1])
        name       = srv.name
        public_ip  = srv.public_ips[0]
        private_ip = srv.private_ips[0]
        manager_ip = [
          for s in values(kamatera_server.server) : s.private_ips[0]
          if can(regex(var.manager_id, s.name))
        ][0]
      }
    }
  }
}
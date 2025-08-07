# Kamatera Output : virtual machines
output "kamatera_output" {
  value = {
    virtualmachines = [
      for key, srv in kamatera_server.server : {
        kind        = "virtualmachine"
        type        = split("-", key)[0]
        index       = split("-", key)[1]
        label       = "${split("-", key)[0]}-${tonumber(split("-", key)[1])}"
        name        = srv.name
        resourceid  = srv.tags.resourceid
        public_ip   = srv.public_ips[0]
        private_ip  = srv.private_ips[0]
        manager_ip  = [
          for s in values(kamatera_server.server) : s.private_ips[0]
          if can(regex(var.manager_id, s.name))
        ][0]
      }
    ]
  }
}

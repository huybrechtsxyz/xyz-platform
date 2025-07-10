# Output the server details
# Name example: srv-shared-manager-1-1234
# Name example: srv-shared-infra-2-1234
# Name example: srv-shared-worker-3-1234
output "serverdata" {
  value = {
    include = [
      for key, srv in kamatera_server.server : {
        role       = split("-", key)[0]
        index      = split("-", key)[1]
        label      = "${split("-", key)[0]}_${tonumber(split("-", key)[1])}"
        name       = srv.name
        ip         = srv.public_ips[0]
        private_ip = srv.private_ips[0]
        manager_ip = [
          for s in values(kamatera_server.server) : s.private_ips[0]
          if can(regex("-manager-1-", s.name))
        ][0]
      }
    ]
  }
}
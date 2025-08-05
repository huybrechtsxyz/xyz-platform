# Output the server details
# {
#   "include": [
#     {
#       "index": "1",
#       "ip": "123...",
#       "label": "infra-1",
#       "manager_ip": "10.0.0.1",
#       "name": "srv-platform-infra-1-random1234",
#       "private_ip": "10.0.0.4",
#       "role": "infra"
#     }
#   ]
# }
# Name example: srv-shared-worker-3-1234
output "kamatera_serverdata" {
  value = {
    include = [
      for key, srv in kamatera_server.server : {
        role       = split("-", key)[0]
        index      = split("-", key)[1]
        label      = "${split("-", key)[0]}-${tonumber(split("-", key)[1])}"
        name       = srv.name
        ip         = srv.public_ips[0]
        private_ip = srv.private_ips[0]
        manager_ip = [
          for s in values(kamatera_server.server) : s.private_ips[0]
          #if can(regex(var.manager_id, s.name))
          if can(regex("-manager-1-", s.name))
        ][0]
      }
    ]
  }
}

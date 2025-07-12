# Output the server details
# {
#   "include": [
#     {
#       "index": "1",
#       "ip": "123...",
#       "label": "infra_1",
#       "manager_ip": "10.0.0.1",
#       "name": "srv-platform-infra-1-jesj",
#       "private_ip": "10.0.0.4",
#       "role": "infra"
#     },
#     {
#       "index": "2",
#       "ip": "123...",
#       "label": "infra_2",
#       "manager_ip": "10.0.0.1",
#       "name": "srv-platform-infra-2-jesj",
#       "private_ip": "10.0.0.5",
#       "role": "infra"
#     },
#     {
#       "index": "1",
#       "ip": "123...",
#       "label": "manager_1",
#       "manager_ip": "10.0.0.1",
#       "name": "srv-platform-manager-1-jesj",
#       "private_ip": "10.0.0.1",
#       "role": "manager"
#     },
#     {
#       "index": "1",
#       "ip": "123...",
#       "label": "worker_1",
#       "manager_ip": "10.0.0.1",
#       "name": "srv-platform-worker-1-jesj",
#       "private_ip": "10.0.0.3",
#       "role": "worker"
#     },
#     {
#       "index": "2",
#       "ip": "123...",
#       "label": "worker_2",
#       "manager_ip": "10.0.0.1",
#       "name": "srv-platform-worker-2-jesj",
#       "private_ip": "10.0.0.2",
#       "role": "worker"
#     }
#   ]
# }
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
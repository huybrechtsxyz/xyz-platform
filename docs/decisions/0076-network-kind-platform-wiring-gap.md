# Network Kind (`kind: network`) — Missing Platform-Wiring

- Status: partially-implemented — wiring and test coverage done; var/secret parity and doc review still open
- Date: 2026-09-09
- Related: ADR-0001 (Kubernetes-style YAML schema), ADR-0003 (layered architecture)

## Context and Problem Statement

`kind: network` is a fully modeled, validated, and documented YAML kind for
declaring VPC/VNet topology (networks, subnets, CIDR peerings). It follows the
same shape as `kind: dns` and `kind: firewall` — a `WorkspaceNetworkModel` file
reference in `workspace.spec.networks`, a dedicated service, and a converter to a
platform-artifact model.

Unlike `dns` and `firewall`, however, network files declared in a workspace are
**never loaded, never merged into `platform.json`, and never reach Terraform or
Ansible** at build time. The feature looks complete end-to-end (schema, docs,
tfvars-emitting code all exist) but the middle link — loading the referenced
files and attaching them to the assembled `PlatformArtifactModel` — was never
written. A user who authors `kind: network` files and references them from
`workspace.yaml` gets no validation error and no build error, and no network
topology in build output. The failure is silent.

### What exists today

| Layer                       | Component                                                                                                                                                             | State                                                                                  |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Schema                      | [`NetworkModel` / `NetworkSpecModel` / `NetworkDefinitionModel`](../../src/strata/models/network_model.py)                                                            | Complete — CIDR overlap validation, peering-target validation, var/secret CIDR sources |
| Loader dispatch             | [`unknown_service.py`](../../src/strata/services/unknown_service.py#L82) → `NetworkService`                                                                           | Complete — `kind: network` files validate correctly in isolation                       |
| Service                     | [`NetworkService`](../../src/strata/services/network_service.py) incl. `merge_networkfiles()`                                                                         | Complete                                                                               |
| Workspace reference         | [`WorkspaceNetworkModel`](../../src/strata/models/workspace_model.py#L167), `workspace.spec.networks` field ([L598](../../src/strata/models/workspace_model.py#L598)) | Schema exists; same shape as `spec.dns_zones` / `spec.firewalls`                       |
| Platform-artifact converter | [`PlatformNetworkModel.from_network_model()`](../../src/strata/models/platform_artifact_model.py#L199)                                                                | Complete but never called                                                              |
| Terraform tfvars            | [`TerraformBuilder._build_network_vars()`](../../src/strata/builders/terraform_builder.py#L645)                                                                       | Complete, reads `platform.spec.networks` — always empty in practice                    |
| Ansible vars                | [`AnsibleBuilder._build_network_vars()`](../../src/strata/builders/ansible_builder.py#L628)                                                                           | Complete, same problem                                                                 |
| Docs                        | [`docs/config/network.md`](../config/network.md)                                                                                                                      | Documents the schema as if fully wired; doesn't disclose the gap                       |

### What is missing

1. **`WorkspaceService` never loads `workspace.spec.networks`.** Compare
   [`workspace_service.py` L529](../../src/strata/services/workspace_service.py#L529):
   ```python
   _load_simple_services(workspace.spec.dns_zones, DnsService, "dns_zones", "DNS zones")
   _load_simple_services(workspace.spec.firewalls, FirewallService, "firewalls", "firewalls")
   _load_simple_services(workspace.spec.providers, ProviderService, "providers", "providers")
   _load_simple_services(workspace.spec.namespaces, NamespaceService, "namespaces", "namespaces")
   ```
   There is no equivalent call for `workspace.spec.networks`, and no
   `get_network_services()` accessor exists at all (`get_dns_services()`,
   `get_provider_services()`, etc. all have one).

2. **`PlatformBuilder` never assembles `platform.spec.networks`.**
   [`platform_builder.py`](../../src/strata/builders/platform_builder.py) doesn't
   even import `PlatformNetworkModel`, and has no block mirroring its DNS-zone
   wiring (`workspace_service.get_dns_services()` →
   `PlatformDnsModel.from_dns_model(...)`, around
   [L451](../../src/strata/builders/platform_builder.py#L451)).

3. **Zero test coverage of the real pipeline masked the gap.**
   `tests/strata/builders/test_builders_ansible.py` (L285) and the terraform
   builder tests construct a `PlatformArtifactModel` directly and hand-set
   `platform.spec.networks = [...]` to test `_build_network_vars()` in isolation.
   `tests/strata/builders/test_builders_platform.py` (the suite that exercises
   `PlatformBuilder._build_spec()`) has **no** network assertions at all — so
   nothing has ever verified that a `workspace.yaml` with a `networks:` file
   reference produces a non-empty `platform.spec.networks`.

### Impact

- Authoring `kind: network` files and referencing them from a workspace is
  **silently a no-op** — no validation or build error, no network topology in
  `platform.json`, `networks.auto.tfvars.json`, or Ansible `strata_networks`.
- The diagram feature ([ADR-0034](0034-diagram-visualization-in-vscode-extension.md))
  reads network files directly via `NetworkService` for visualization, so
  diagrams *can* render a network topology that never actually reaches a real
  build — compounding the confusion.

## Considered Options

- **Option A — Wire it up** (mirror the existing DNS/firewall pattern exactly):
  add `get_network_services()` to `WorkspaceService`, call
  `_load_simple_services(workspace.spec.networks, NetworkService, "networks", "networks")`,
  and add a `platform_builder.py` block that converts loaded network services to
  `PlatformNetworkModel` and assigns `platform.spec.networks`. Add
  `test_builders_platform.py` coverage exercising a real workspace with
  `networks:` file references end-to-end.
- **Option B — Remove the unfinished feature**: delete `kind: network`, its
  schema, docs, and the now-dead `_build_network_vars()` consumers in both
  builders, until there is a concrete need and it can be built end-to-end in one
  pass.
- **Option C — Document as unsupported and defer**: keep the schema (useful
  today only for diagram visualization per ADR-0034) but explicitly mark
  `docs/config/network.md` and the schema description as "not yet wired into
  build output" so users aren't misled, without committing to finishing it now.

## Decision Outcome

Chosen: **Option A**, because every piece needed already exists (schema,
service, converter, tfvars builders in both Terraform and Ansible) — the
remaining work is purely the two loading/assembly call sites plus one test
suite, mirroring an established pattern (DNS/firewall) with no new design
required. Options B and C both leave working code (schema, converters, tfvars
builders) unused or actively misleading for no benefit.

### Consequences

- Good: `kind: network` becomes fully functional — declared topology reaches
  `platform.json` and both provisioners' variable files, matching its
  documentation.
- Good: closes a silent-failure trap (no error today when the feature doesn't
  work).
- Bad: none of the CIDR/peering data has ever flowed through a real build, so
  wiring it up may surface latent bugs in `_build_network_vars()` that only
  existed on paper until now (e.g. var/secret resolution paths, peering target
  cross-references against merged multi-file network sets).

## Remaining Work

- [x] Add `WorkspaceService.get_network_services()` (mirrors `get_dns_services()`).
- [x] Add `_load_simple_services(workspace.spec.networks, NetworkService, "networks", "networks")`
      call in `WorkspaceService` alongside the existing DNS/firewall/provider/namespace calls.
- [x] Add a `platform_builder.py` block that builds `platform.spec.networks` from
      `workspace_service.get_network_services()`, importing `PlatformNetworkModel`.
- [x] Add `test_builders_platform.py` coverage: a workspace referencing a
      `kind: network` file end-to-end produces a populated `platform.spec.networks`
      (`TestPlatformBuilderNetworks`). Also added `test_services_workspace.py` coverage
      (`TestWorkspaceServiceNetworks`) for the loader/accessor wiring itself.
- [ ] Re-verify `TerraformBuilder._build_network_vars()` and
      `AnsibleBuilder._build_network_vars()` against a real (not hand-built)
      `PlatformArtifactModel` — resolve any var/secret/peering issues surfaced by
      real data. Confirmed gap: `AnsibleBuilder._build_network_vars()` only handles
      literal `value:` CIDRs (no `var:`/`secret:` resolution), unlike its Terraform
      counterpart which resolves `var:` — worth aligning for parity.
- [ ] Update `docs/config/network.md` if any behavior differs from what's
      currently documented once the real pipeline is exercised.

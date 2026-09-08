# strata — Changelog

Concise, user-facing summary of each release. Full implementation detail (per-phase notes,file/method names, bug-fix specifics) lives in [HISTORY.md](./HISTORY.md). Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [1.9.6] - 2026-09-08

### Fixed

- **`strata build run` never resolved `spec.extends` (ADR-0039)**, unlike `deploy run` and `validate --deep` — a leaf deployment file that passed `validate --deep` still failed `build run` with "Workspace not found in deployment" because inherited fields (`workspace`, `stages`, ...) were never merged in. Fixed via a shared resolver helper now used by both build and deploy; `build run` also rejects partial deployments the same way `deploy run` already did, and this failure (and others like it) is now properly surfaced in `get_validation_errors()` instead of only appearing in the stderr log.
- **`RemoteModel.deploy_path` was optional but effectively mandatory for gitops remotes** — an unset value silently dropped the remote from `@remote/...` resolution and made repo-fetch checks look for the wrong local path. Now required for `type: gitops`, failing loud at config-validation time instead of at deploy time with a misleading error.

## [1.9.5] - 2026-09-08

### Added

- **Deployment change-reference tracking (ADR-0074)** — `deploy run`/`deploy destroy` accept `--change-id`/`--reason`/`--change-system`/`--change-title`/`--change-url`/`--change-classification` (or `STRATA_CHANGE_*` env vars), recorded on the deployment manifest/log; a new `change_reference_required` policy can enforce it before any deploy or destroy.

### Fixed

- **Terraform backend config could silently deploy with an unresolved `${var:}`/`${secret:}` reference, and Helm value substitution only matched inside `env:`-keyed dicts with an exact whole-value match** — both provisioners now share one typed `${var:}`/`${secret:}`/`${feature:}` resolver (ADR-0075) that fails loud on any unresolved reference and works anywhere in the document, plus a matching build-time check. **Breaking:** removes Helm's old untyped `${KEY}` syntax.

## [1.9.4] - 2026-09-07

### Fixed

- **Every CLI invocation crashed on Windows** (`SystemError: ConsoleRenderer with colors=True ... requires colorama`) — `colorama` wasn't declared as a dependency. Added it (Windows-only); the console formatter also now falls back to uncolored output instead of crashing if it's still missing.
- **Partial deployments (`spec.partial: true`) never checked whether their `environments[]` file references existed**, since full Phase 2 validation is intentionally skipped for them — dangling references went undetected until a leaf deployment extending the partial file was built/deployed. A lightweight existence-only check now runs for partial files under `--deep` validation.
- **Layer segment values could capture a deployment file's own filename as a directory segment** when the file sat exactly one level shallower than its convention's segment depth — segment value extraction now strips the filename first. See ADR-0072.

## [1.9.3] - 2026-09-04

### Fixed

- **Bicep provisioners had no builder-side copy step**, so `strata build run` produced nothing for them and `deploy run` always failed — new `BicepBuilder` copies `.bicep` source like every other provisioner. See ADR-0071.
- **`source.reference` (git-ref pinning) was silently ignored by Ansible and Helm's local-chart copy** — both now honor a pinned ref via a shared `BaseBuilder` helper.
- **`WorkspaceIacModel.backend`/`.output` validated on non-Terraform provisioners despite having zero effect** — now rejected at validation time.
- **`argocd`/`flux` sync provisioners wrongly required a `source:` block** — an enum/string comparison bug always evaluated the "not required for sync" check false. Fixed.
- **`strata deploy lock status`/`release`/`history` could act on the wrong Terraform provisioner's lock** in multi-provisioner workspaces — removed a duplicate, non-stage-aware resolver; all lock subcommands now match `deploy run`'s stage-aware resolution.

### Changed

- **Terraform/Ansible/Bicep/Helm/Compose/Sync now resolve build output paths through one canonical `SolutionController` helper** instead of each independently re-deriving the shape (ADR-0071). No user-facing behavior change.

## [1.9.2] - 2026-09-02

### Fixed

- **`environment.spec.properties`/`custom`/`overrides.properties` were shallow-merged across environment files, silently dropping nested sibling keys on override** — now uses a shared `deep_merge()` (new `strata.utils.dict_merge`), consistent with the Terraform tfvars merge which already did this correctly.
- **Resource/module `configuration`/`custom` overrides had the same shallow-merge bug**, despite comments claiming "Deep merge" — fixed to use the same shared `deep_merge()`.

## [1.9.1] - 2026-09-02

### Changed

- **BREAKING: `configuration.spec.layering`/`spec.layerings` removed, merged into `spec.paths`; `deployment.spec.layers` reshaped to `{follows, segments}`** — replaces two inconsistent path-matching engines (plain `fnmatch` vs. a genuinely segment-aware matcher) with one convention model. No backward-compatible fallback — models use `extra: forbid`, so an unmigrated config fails fast with an "unknown field" error. See [configuration.md](../docs/config/configuration.md) and ADR-0072.

### Added

- **`spec.custom` and dict-aware `validate:` rules** — `validate:` rules can now reach into freeform `spec.configuration`/`spec.properties`/`spec.custom` dicts, which previously validated nothing silently.
- **`strata deploy run --namespace NAME` (repeatable)** scopes the helm provisioner to specific namespace(s) for a single run, via a new `stages[].helm_namespaces` allowlist. See [deployment.md](../docs/config/deployment.md#namespace-vs-helm_namespaces--kubernetes-namespace-scoping).

## [1.8.3] - 2026-08-27

### Fixed

- **`strata deploy run` always failed with `ServiceNotValidatedError: Service 'EnvironmentService' must be validated before use` (regression since v1.7.0)** — `RunDeployCommand` had a stale no-op `_load_related_services()` override (`return True`, loading nothing) left over from before that method became an overridable hook for lightweight, environment-only commands (`deploy show`, `values get`/`list`/`resolve`). Once the base class started calling that hook instead of loading services directly, `deploy run`'s override silently skipped the entire workspace + environment load — `strata validate --deep` and `strata build run` on the same file both succeeded, masking the bug until `deploy run` crashed immediately on every invocation. Removed the incorrect override so `deploy run` inherits the base class's real, full-load implementation, matching its pre-v1.7.0 behavior.

## [1.8.2] - 2026-08-25

### Fixed

- **Terraform input validation flagged secrets belonging to a different provisioner as errors** — `strata build run`'s `variables.tf` cross-check (`TerraformBuilder._collect_declared_input_keys()`, added in v1.7.0) swept every secret declared anywhere in `environment.yaml` into every Terraform provisioner's "declared inputs" set, with no scoping by stage or provisioner. In any workspace intentionally sharing one `environment.yaml` across multiple provisioner types (Terraform for infra + Ansible/Compose/Helm for app secrets — a standard pattern given strata's own stage-level `secrets:` allowlist design), this unconditionally failed the build the moment a single non-Terraform secret existed in the shared file. Declared secrets are now scoped to the stage(s) that actually resolve to that Terraform provisioner (by `stage.provisioner` name, `stage.topology` → `topology.provisioner`, or the sole workspace provisioner), mirroring `ResolvedValues.for_stage()`'s identical `stage.secrets` allowlist already used at deploy time. Variables and features are unaffected (never stage-scoped at deploy time, so they remain a global check). Workspaces with no stages at all, or where a provisioner isn't referenced by any stage, keep the previous unscoped behavior (no regression for setups not yet using multi-provisioner stage scoping).
- **Local Helm charts with standard Go-template syntax crashed the entire build** — `strata build run` copies a local chart's source directory (`spec.source.repository`/`source_path`, no `chart_repository`) into the build output, then Jinja2-renders every text file in it for strata's own `${STRATA_*}`/`variables.*`/`features.*` substitution. Since that substitution pass is effectively always active once a deployment is loaded, any chart whose `templates/` directory used real Helm Go-template syntax (e.g. `{{ .Release.Name }}`) failed with `TemplateSyntaxError: unexpected '.'`, aborting the build — making the documented local-chart feature unusable for any real-world chart. `templates/` (Helm's own template scope, rendered by Helm itself at deploy time) is now always excluded from strata's templating pass; as defense in depth, any other file that isn't valid Jinja2 is now skipped with a warning instead of crashing the build.

### Added

- **Helm `${TOKEN}` secret substitution now works at any nesting depth, not just `entry.env.KEY`** — `HelmDeployer`'s `${KEY}` → `--set-string` substitution previously only matched a top-level entry's `env` sub-dict (strata's own `svc.environment`-generated shape), so any real-world chart with chart-mandated deep nesting (e.g. Immich's `controllers.main.containers.main.env.DB_PASSWORD`) or a flat `{env: {...}, image: {...}}` shape had no way to opt into token substitution at all. `_find_env_tokens()` now walks the whole values document looking for any dict node keyed literally `env` (dict-shaped: `env: {KEY: value}`), at any depth — still scoped to `env`-keyed dicts specifically (not an unrestricted tree walk) to avoid matching a user-typed `${...}`-shaped string in unrelated pass-through configuration. The Kubernetes-native list shape (`env: [{name: KEY, value: value}]`) remains unsupported.

## [1.8.1] - 2026-08-25

### Fixed

- Corrected `VERSION.txt`, which had been bumped to the invalid `1.8.0-alpha` prerelease suffix in the Kroki integration commit — setuptools' dynamic-version PEP 440 normalization turns this into `1.8.0a0` (no hyphen) at install time, which fails `strata`'s own strict-semver self-checks (`tests/strata/commands/test_version.py`). No prior release in this repo's history had ever used a prerelease suffix; reverted to the established plain `X.Y.Z` convention. No functional changes.

## [1.8.0] - 2026-08-25

### Added

- **`strata diagram show --format svg|png` via Kroki (ADR-0034)** — new `kroki` integration (`diagram_render` capability) renders Mermaid diagrams to real SVG/PNG image files. Zero-config by default (public `https://kroki.io`, no account/API key/CLI install needed); self-hostable via `STRATA_KROKI_ADDRESS` or a declared `type: kroki` integration with a custom `endpoints.address`. `--format` is a new flag distinct from `--output` (which still controls the console/json/text response envelope). See `strata help --topic kroki`.
- **4 new built-in diagrams completing the non-flowchart cookbook set (ADR-0034)** — `drift-summary` (`pie`), `gate-sequence` (`sequence`), `environment-complexity` (`quadrant`), and `secret-store-flow` (`sankey`) join `timeline` (`gantt`) as the first worked example of each non-flowchart Mermaid type. All hand-written `spec.template` (these types aren't sugar-generatable) and honestly scoped to data the existing `drift`/`approvals`/`environments`/`secrets` sources already expose — no new source types were added.
- **VS Code Diagram Builder + `/diagram create` chat generation (ADR-0034 Phase 4)** — `strata diagram show --output json` now always includes the parsed `sources`/`layout`/`style` (and a `has_template` flag) alongside the rendered output, enabling the VS Code extension's new visual Diagram Builder to round-trip a sugar-based definition without re-parsing YAML client-side. See the VS Code extension changelog for the Builder itself.
- **8 new built-in diagrams (ADR-0034 Phase 3)** — `strata diagram show -f <name>` now ships `stages`, `promotion`, `network`, `services`, `environments`, `secrets`, `timeline`, and `architecture` alongside the existing `refs`/`topology`, covering the ADR's full "Top 10" built-in list. Most are defined with zero Jinja — just `spec.sources` + `spec.layout`/`spec.style` — the generator sugar's first real end-to-end use in a shipped built-in. `timeline` is the first shipped `gantt`-type diagram (milestones from deploy history, since the audit trail has no per-stage duration).
- **`strata validate --deep` catches broken `strata://` links in hand-authored diagrams (ADR-0034)** — a `kind: diagram` with a hand-written `spec.template` containing a `click <id> "strata://..."` line now has that link checked against the workspace; a renamed/removed target is reported as a validation error instead of only being discovered by clicking a dead node in the VS Code diagram preview. Generated diagrams (no `spec.template`, built from `spec.layout`/`spec.style`) are unaffected — their URIs are always freshly computed at render time.

### Fixed

- **`strata diagram show -f refs` (and `-f topology`) resolved file references relative to the referencing file's directory instead of the workspace root** — `GraphController` joined `spec.workspace.file`, resource/module/namespace/network/firewall/dns `file:` references against `source_file.parent` rather than `work_path`, doubling the path prefix for any reference nested below the workspace root (e.g. `config/config/stack/workspace.yaml`) and marking every such node `:::missing`. File references in strata YAML are always workspace-root-relative, matching `BaseService._resolve_file_path()`. See ADR-0015.
- **Required-integration validation could false-fail commands unrelated to the integration's capability (ADR-0069)** — `strata values get` and similar commands previously failed on a `required: true` `terraform` integration they never needed (they only need secret/variable/feature stores). `IntegrationService.validate_required_integrations()` now accepts an optional capability filter, and each command scopes validation to only the capabilities it actually needs — fixes the false failure without weakening validation for commands (`build`/`deploy`) that do need the provisioner.

## [1.7.0] - 2026-08-12

### Breaking Changes

- **Cost estimation now requires an `infracost` integration declaration** — `strata cost show`, `strata cost diff`, and the automatic post-plan cost diff in `deploy run --dry-run` previously worked off of any `infracost` binary found on PATH, regardless of `configuration.yaml`. They now require an explicit `infracost` entry under `spec.integrations` (with `capabilities: [cost]`, `enabled: true`), matching how every other integration (secret stores, provisioners) is gated. An installed binary with no declaration no longer does anything. `strata cost history` is unaffected — it reads past `cost show` snapshots and needs no estimator.

### Added

- **CLI login for a control plane or any OIDC service (ADR-0067)** — new `identity` integration capability with six first-class providers (`azure_ad`, `google`, `aws_identity_center`, `auth0`, `github_oauth`, `generic_oidc`), declared under `spec.integrations` like any other integration. No dedicated `strata login` command — login triggers lazily and is checked/driven via `strata sln doctor --deep --login`. When a control-plane session is active, its authenticated identity outranks the CLI-local `actor` resolution chain from ADR-0066. See ADR-0067 and `strata help --topic identity`.
- **Provisioner-managed resources** — workspace resources can now declare `managed_by: provisioner` to indicate the provisioner (Terraform/Ansible) fully owns the resource definition. No resource file is required. This simplifies multi-tenant IaC deployments where all resource details (VMs, databases, networks) are defined entirely in Terraform modules. The resource declaration still participates in topology wiring, but strata skips spec validation and file loading.
- **Git ref pinning on `SourceModel` (ADR-0063, Gap 1)** — Provisioner sources now accept an optional `reference` field (branch, tag, or commit SHA) that overrides the workspace-level remote default. This allows two provisioners referencing the same remote to pin different versions (e.g., platform baseline on `v1.4.0` and team module on `main`). Resolution priority: `source.reference` → environment remote override → remote default. When a ref is pinned, `git archive` extracts the subtree without mutating the working tree.
- **Structured variable types (ADR-0063, Gap 2)** — `VariableStoreModel` now accepts an optional `type` field (`string`, `number`, `bool`, `object`, `list`, `map`) that declares the intended HCL type. When set, strata validates that the YAML value matches the declared type and emits it as a native JSON type in `.auto.tfvars.json` instead of always stringifying. Complex values can now be authored as native YAML mappings/sequences instead of JSON-in-string.
- **Terraform input validation (ADR-0063, Gap 3)** — `strata build run` now cross-checks declared variable/feature/secret keys from environment YAML against the module's `variables.tf` declarations. Undeclared inputs (typos) are errors that block the build with fuzzy-match suggestions. Unsupplied required variables (no default) are reported as warnings. Eliminates a class of silent deployment failures where misspelled variable names were silently dropped by Terraform.
- **Helm values validation (ADR-0063, Gap 3)** — For local Helm charts, `strata build run` now cross-checks `module.spec.configuration` keys against the chart's default `values.yaml`. Typos in top-level and one-level-deep keys are reported with fuzzy-match suggestions. Warnings only (does not block build). Registry charts are skipped (not available at build time).
- **Output passing between provisioners (ADR-0063, Gap 4)** — Provisioners now accept an `inputs_from` field that declares explicit dependencies on other provisioners' outputs. Supports `mapping` (key rename), `prefix` (add prefix to all keys), and `select` (allowlist) modes. Validated at schema level: unknown provisioner references, self-references, and circular dependencies are rejected. Mapped keys are treated as "supplied" by Gap 3 input validation. The existing `stage_outputs` injection mechanism applies the mapping at deploy time.
- **Combined deployment outputs artifact (ADR-0063, Gap 5)** — After a successful `deploy run`, strata now writes a `deployment-outputs.json` file that merges all stages' Terraform outputs into a single registry-consumable document. Outputs are keyed by stage name; sensitive output keys are listed but values omitted. Includes deployment metadata (name, version, workspace, environment, tenant) and provenance (completed stages). The artifact is the contract surface for service registry integration.

---

## [1.6.1] - 2026-08-03

### Added

- **SQLite-backed resolved-model cache — `strata cache` (ADR-0026)** — deployments' resolved model is now cached keyed by a hash of its source YAML files, so `build run`/`build plan`/`policy check` don't need to re-resolve unchanged deployments. New `strata cache warm/status/clear/export` commands; the affected build/policy commands auto-warm on success (`--no-cache-warm` to opt out). The VS Code extension warms the cache in the background on save and startup. See ADR-0026.

### Fixed

- **`deploy run`/`deploy destroy` exit codes for invalid deployments** — a deployment file that fails schema/cross-reference validation now correctly exits 3 (was 1); a missing/unresolvable deployment file now exits 2. See ADR-0004.

### Security

- **Secret/variable/feature store outages no longer conflated with "not found"** — integrations (Infisical, HashiCorp Vault/OpenBao, Bitwarden, Azure Key Vault) now raise a new `SecretStoreUnavailableError` on connectivity/auth failures instead of returning `None`. Previously a transient outage could be mistaken for a missing secret, triggering silent auto-generation of a fresh value (for `generate:` secrets) or letting a deploy proceed with a blank value. `deploy run` now aborts instead of continuing when a store is unavailable.
- **Pre-flight availability checks for secret stores and provisioners** — `deploy run` now verifies all referenced secret/variable/feature stores and required provisioner tools (terraform, ansible, etc.) are reachable before resolving any values or acquiring the deployment lock, failing fast instead of partway through a multi-stage deploy.

---

## [1.6.0] - 2026-07-31

### Breaking Changes

- **`env` command group removed (ADR-0062)** — its six commands are redistributed: `env show`/`env output`/`env drift` merge into the existing `deploy` equivalents, `deploy status` is revived with corrected live-state behavior, `env status --all/--path` becomes the new `rollout status` group, and `env info`/`env doctor` move to `sln status`/`sln doctor`. No deprecation shim — update any `strata env ...` usage in scripts, pipelines, or MCP tool calls.
- **Unified `spec.gates` schema (ADR-0059)** — `spec.approvals`/`approvers` and the separate ADR-0057 `spec.gates` block are merged into a single deployment-level `spec.gates` list with typed approver refs (`github-team`/`user`/`ado-group`). Existing YAML using either old shape fails validation; see ADR-0059 for the field-by-field migration mapping.

### Fixed

- **`ref_convention` policy / `strata repo status` design drift (ADR-0017)** — tag naming conventions now live in one place, `spec.remotes[].conventions` (`RemoteConventionsModel`), instead of being duplicated inside the policy's own config. `repo status` no longer guesses release/quality tags from hardcoded name prefixes — it links a local repo to its configured remote by comparing normalized git remote URLs and only classifies tags when that remote declares `conventions`. No backward compatibility with the old policy-level `configuration.remotes[]` shape (never released/documented before this fix).

### Changed

- Subprocess execution consolidated onto a single `run_command()` path with consistent SIGTERM handling and timeout parity across builders, deployers, and controllers (ADR-0061).

## [1.5.0] - 2026-07-27

### Added

- Deployment workflow orchestration — approval/cost/security gates, work-item lifecycle (`strata workitem`), `deploy run --resume`, exit code 5 for hand-off. See ADR-0057.
- Comprehensive help documentation for all 12 platform YAML kinds; `docs/help/` is now the single source of truth, synced to the CLI and VS Code extension at build time.
- AI agent integration — advisory LLM analysis across build/deploy/validate/policy/audit commands via `--ai`, plus VS Code chat participant commands (`/review`, `/diagnose`, `/sbom`). See ADR-0025.

## [1.4.0] - 2026-07-24

### Added

- `--timeout` for `deploy run`/`deploy destroy` (ADR-0027) and SIGTERM graceful shutdown (ADR-0028).
- Cloud CLI integrations + lifecycle scripts for GCP (ADR-0055), AWS, and Azure.
- Scoped multi-scheme layering (ADR-0042), path convention validation (ADR-0052), Checkov IaC security scanning (ADR-0051), Bicep provisioner (ADR-0046), Azure CLI integration (ADR-0053), OPA policy integration (ADR-0050).
- UTC datetime standardization across all timestamps (ADR-0045).

### Changed

- Deployment layers no longer require the final layer to be named `"environment"`; `spec.layering` deprecated in favor of `spec.layerings`.

### Breaking Changes

- Removed hardcoded final-layer-name constraint — review configs that relied on it.

## [1.3.1] — 2026-07-22

### Added

- Cost estimation via Infracost — `strata cost show/diff/history`, `cost_threshold` policy (ADR-0031 Phase 1).
- VS Code extension reworked around deployment-centric UX — Deployments/Operations views replace the flat Files view.

### Changed

- Removed unused provider `engine` and resource `unit_cost` fields.

## [1.2.1] — 2026-07-20

### Changed

- `strata new --output-file` (was `--path`); `strata validate run --pattern` (was `--path`).
- Exit code 4 for deployment lock conflicts.

### Fixed

- `strata secret mask` positional-argument dash handling; Sphinx docs build warnings.

## [1.2.0] — 2026-07-16

### Added

- GitOps controller integration — `argocd`/`flux` provisioner types with reconciliation health checks (ADR-0041).
- Platform artifact convenience fields — name, labels, revision, resolved_variables, chart/image versions.

### Changed

- `WorkspaceIacModel.source` is now optional for sync-type provisioners.

## [1.1.1] — 2026-07-15

### Added

- `strata new --validate` — validates generated files immediately after creation.

### Changed

- Command lifecycle migrated to `_execute()` across the entire command layer; `execute()` sealed on `BaseCommand` (ADR-0030).

## [1.1.0] — 2026-07-14

### Added

- Environment provider overrides — per-environment provider file/configuration swaps (ADR-0036).
- Promotion strategy framework groundwork (ADR-0011); tenant scaffolding bundle template (`strata new tenant`).

## [1.0.1] — 2026-07-09

### Added

- Tenant `spec.environments`/`spec.properties`/`spec.custom` now applied at build time.
- `sln deployment` subcommand group (`add`/`remove`/`list`/`scan`).

### Fixed

- Tenant `spec.environments` was validated but never merged into the build pipeline.

## [1.0.0] — 2026-07-08

### Added

- S3 distributed lock backend (ADR-0007).
- VS Code extension reached CLI feature parity — Values Inspector, lock status/release, drift detection, SBOM generation, stage-targeted deploy, repository write operations.
- Umbrella JSON schema (`strata.json`) — a single schema entry replaces 12 per-kind entries.

### Changed

- Unified exception handling and structured logging across all commands.

## [0.16.1] — 2026-07-06

### Fixed

- `uv sync --group doc` dependency group resolution.

## [0.16.0] — 2026-07-05

### Added

- Environment composition — flat merge now covers all 8 spec sections with provenance tracking (`values list --trace`) (ADR-0024).

## [0.15.0] — 2026-07-03

### Added

- Configurable Terraform build output profiles (ADR-0019).
- `strata env status` (renamed from `env state`); VS Code Environments and Audit Trail panels.

## [0.14.0] — 2026-06-26

### Added

- Deployment audit and traceability — SIEM sinks for Sentinel, ELK, and OpenTelemetry (ADR-0018).

## [0.13.0] — 2026-06-24

### Added

- Guided onboarding experience — `strata console` REPL, `validate graph`/`--explain`, batch validation (ADR-0014).

## [0.12.0] — 2026-06-23

### Added

- Auto-generated secrets and seed-on-missing for variables/features (ADR-0013).

## [0.11.0] — 2026-06-23

### Added

- Promotion strategies system — waves, progressions, `strata promote` (ADR-0011).

### Changed — Breaking

- Renamed `customer` → `tenant` throughout the platform (ADR-0012). See migration guide in [HISTORY.md](./HISTORY.md).

## [0.10.0] — 2026-06-22

### Added

- `strata deploy show`/`list`, bundle templates for `strata new`, cross-manifest overlap validation (`validate --path`), remote reference overrides per environment.

## [0.9.3] — 2026-06-20

### Added

- Helm module build/deploy improvements — chart coordinates in `meta.yaml`; `--wait`/`--atomic` on `helm upgrade`.

## [0.9.2] — 2026-06-19

### Fixed

- Ansible builder no longer writes empty variable files for unused sections.

## [0.9.1] — 2026-06-19

### Changed

- SBOM collector warnings are silenced by default (use `--verbose` to see them).

## [0.9.0] — 2026-06-18

### Breaking Changes

- Configuration YAML: `spec.repositories` renamed to `spec.remotes` (ADR-0010).

### Added

- Self-SBOM generation in CI; SBOM attached to every GitHub Release.

## [0.8.2] — 2026-06-17

### Fixed

- CI docs workflow dependency group and tag-trigger condition.

## [0.8.1] — 2026-06-17

### Added

- GitHub Pages CI workflow for documentation.

### Fixed

- Topology volume `type` field restriction removed.

## [0.8.0] — 2026-06-17

### Added

- CVE audit (`strata build sbom --audit`) via Trivy/Grype; `sbom_license` policy; 6 new lockfile parsers (NuGet, Maven, Gradle, Gemfile, Cargo, Composer); `.strata/lockfile_parsers/` auto-discovery.

## [0.7.0] — 2026-06-16

### Added

- `strata policy check` standalone command; `strata deploy outputs`; deploy-phase policy hook.

## [0.6.0] — 2026-06-15

### Added

- Policy engine — declarative guardrails at validate/build/plan phases (`customer_zone`, `required_tags`, `naming_pattern`, `script` policy types); `strata policy list`.

## [0.5.0] — 2026-06-12

### Added

- SBOM generation (CycloneDX 1.6) — `strata build sbom`; container image, Helm, Terraform provider, and Ansible collection collectors.

## [0.2.0] — 2026-06-09

### Added

- `strata env status`/`drift`, `strata values set`/`resolve`, environment module overrides (image/chart pinning per environment).

## [0.1.1] — 2026-06-07

### Added

- Cross-module `depends_on` (`@module/service` syntax); `strata output`.

## [0.1.0] — 2026-05-14

_First real release._

- Layered architecture, Pydantic v2 models for all YAML kinds, Click CLI, exit codes 0-3, structured logging.
- Core commands: `validate`, `build`, `deploy`, `diff`, `sln`, `repo`, `profile`, `config`, `vars`, `log`, `tools`, `schema`.
- Terraform subprocess integration; secret resolution (Bitwarden, Azure Key Vault, HashiCorp Vault, env vars).

---

<!--
To release a new version:
- Move entries from [Unreleased] into a new ## [x.y.z] — YYYY-MM-DD section.
- Update VERSION.txt to match.
- Tag: git tag vx.y.z && git push origin vx.y.z
- Keep entries to 1-3 lines per feature, referencing the ADR for detail.
  Full narrative detail goes in HISTORY.md only if truly needed for archaeology.
-->

## Legend

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** in case of vulnerabilities
- **Infrastructure** for CI/CD and build changes
- **Documentation** for doc-only changes

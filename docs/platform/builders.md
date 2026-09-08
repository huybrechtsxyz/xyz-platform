# Builders Documentation

## Overview

Builders assemble and persist deployment artifacts from fully-loaded services. They follow a three-phase lifecycle (`before_build → build → after_build`) and accumulate errors and messages instead of raising exceptions.

**Available builders:**

| Class              | Module                       | Purpose                                                              |
| ------------------ | ---------------------------- | -------------------------------------------------------------------- |
| `BaseBuilder`      | `builders.base_builder`      | Abstract base — error/message accumulation + lifecycle hooks         |
| `PlatformBuilder`  | `builders.strata_builder`    | Assembles `platform.json` / `platform.yaml` from services            |
| `TerraformBuilder` | `builders.terraform_builder` | Generates `.tfvars.json` files for Terraform from the platform model |
| `AnsibleBuilder`   | `builders.ansible_builder`   | Generates `strata_*.yml` YAML variable files for Ansible playbooks   |
| `ComposeBuilder`   | `builders.compose_builder`   | Generates `docker-compose.yml` per namespace for compose modules     |
| `HelmBuilder`      | `builders.helm_builder`      | Generates `values.yaml` and `meta.yaml` per Helm module              |
| `SbomBuilder`      | `builders.sbom_builder`      | Generates CycloneDX 1.6 JSON SBOM from the platform artifact         |

---

## BaseBuilder

Abstract base class that all builders extend.

```python
from abc import ABC, abstractmethod
from pathlib import Path

class BaseBuilder(ABC):
    def __init__(self, verbose: bool = False) -> None: ...

    def has_errors(self) -> bool: ...
    def has_messages(self) -> bool: ...
    def get_errors(self) -> List[str]: ...
    def get_messages(self) -> List[str]: ...

    @abstractmethod
    def build(self, deployment_service, work_path, build_path, dry_run=False) -> bool: ...
    @abstractmethod
    def before_build(self, deployment_service, work_path, build_path) -> bool: ...
    @abstractmethod
    def after_build(self, deployment_service, work_path, build_path, dry_run=False) -> bool: ...
```

### Extending BaseBuilder

```python
from pathlib import Path
from strata.builders.base_builder import BaseBuilder

class MyBuilder(BaseBuilder):
    def before_build(self, deployment_service, work_path, build_path):
        if not deployment_service.is_validated():
            self._errors.append("Service not validated")
            return False
        return True

    def build(self, deployment_service, work_path, build_path, dry_run=False):
        # assemble artifacts
        return True

    def after_build(self, deployment_service, work_path, build_path, dry_run=False):
        # verify output files
        return True
```

---

## PlatformBuilder

Assembles a `PlatformArtifactModel` from a fully-loaded `DeploymentService` hierarchy and writes `platform.json` / `platform.yaml` to the deployment build path.

```python
from pathlib import Path
from strata.builders.strata_builder import PlatformBuilder

builder = PlatformBuilder(verbose=True, configuration_service=config_svc)

if not builder.before_build(deployment_service, work_path, build_path):
    print("Pre-build failed:", builder.get_errors())
elif not builder.build(deployment_service, work_path, build_path, dry_run=False):
    print("Build failed:", builder.get_errors())
elif not builder.after_build(deployment_service, work_path, build_path):
    print("Post-build failed:", builder.get_errors())
else:
    print("Built:", builder._last_platform_model.meta.name)
```

### Constructor

```python
PlatformBuilder(
    verbose: bool = False,
    configuration_service=None,   # optional — used for artifact_path computation
)
```

### Three-Phase Pipeline

#### `before_build(deployment_service, work_path, build_path)` → `bool`

1. Verifies `deployment_service.is_validated()` — error if not validated
2. Verifies `deployment_service.get_workspace_service()` returns a workspace — error if None

#### `build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. Calls `_build_platform(deployment_service)` to assemble the `PlatformArtifactModel` in memory
2. If `dry_run=True`: logs planned output file paths, returns `True` without writing to disk
3. If `dry_run=False`: persists `platform.json` and `platform.yaml` to the deployment build path

The assembled model is stored in `builder._last_platform_model` for downstream use (e.g. passing to `TerraformBuilder.build()`).

#### `after_build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. If `dry_run=True`: returns `True` immediately (skips file-existence checks)
2. Verifies `platform.json` and `platform.yaml` exist in the deployment build path

### Build Path

The deployment build path is `build_path / "{deployment-name}-{deployment-version}"` as returned by `deployment_service.get_build_path(build_path)`.

---

## TerraformBuilder

Generates Terraform `.tfvars.json` files from a `PlatformArtifactModel`. The builder tracks all variable, feature, and secret references encountered and writes requirement manifests alongside the generated artifacts.

> **Security note:** `TerraformBuilder` NEVER writes resolved values for variables, secrets, or features. It only documents which keys are required.

```python
from pathlib import Path
from strata.builders.terraform_builder import TerraformBuilder

builder = TerraformBuilder(verbose=True)

# dry_run with a pre-assembled model (avoids reading platform.json from disk)
if not builder.before_build(deployment_service, work_path, build_path, dry_run=True):
    print("Pre-build failed:", builder.get_errors())
elif not builder.build(
    deployment_service, work_path, build_path,
    dry_run=True,
    platform_model=platform_builder._last_platform_model,
):
    print("Build failed:", builder.get_errors())
```

### Constructor

```python
TerraformBuilder(verbose: bool = False)
```

### Requirement Tracking

The builder accumulates references to variables, features, and secrets during `_build_terraform_vars()`. These are tracked in:

| Attribute       | Type              | Contents                                            |
| --------------- | ----------------- | --------------------------------------------------- |
| `variable_refs` | `Dict[str, dict]` | Variables referenced by resources/providers/modules |
| `feature_refs`  | `Dict[str, dict]` | Feature flags referenced                            |
| `secret_refs`   | `Dict[str, dict]` | Secrets referenced                                  |

Each entry has: `key`, `description`, `required`, `suggested_env_var` (variables only), `used_by`.

The refs are **reset at the start of each `build()` call** to avoid stale data from previous runs.

### Three-Phase Pipeline

#### `before_build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. Verifies `deployment_service.is_validated()` — error if not validated
2. If `dry_run=False`: verifies `platform.json` exists in the deployment build path — error if missing (run `PlatformBuilder` first)

#### `build(deployment_service, work_path, build_path, dry_run=False, platform_model=None)` → `bool`

1. Resets requirement tracking dicts
2. If `platform_model` is supplied: uses it directly (no disk read)
3. If `platform_model=None` and `dry_run=False`: loads `platform.json` from the deployment build path
4. Calls `_build_terraform_vars()` to assemble all payloads
5. If `dry_run=True`: logs planned file paths and requirement counts, returns `True` without writing
6. If `dry_run=False`: writes all files to `{deployment_build_path}/terraform/`

#### `after_build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. If `dry_run=True`: returns `True` immediately
2. Verifies the 7 base artifact files exist in `{deployment_build_path}/terraform/`

### Output Files

All files are written to `{deployment_build_path}/terraform/`:

| File                           | Contents                                            |
| ------------------------------ | --------------------------------------------------- |
| `workspace.auto.tfvars.json`   | Workspace identity, labels, metadata                |
| `providers.auto.tfvars.json`   | Provider configurations keyed by name               |
| `topologies.auto.tfvars.json`  | Topology definitions with components/volumes        |
| `modules.auto.tfvars.json`     | Module source paths and properties                  |
| `resx_{type}.auto.tfvars.json` | Resources grouped by `resource_type` (one per type) |
| `tf_required_variables.json`   | Required variable keys (no values)                  |
| `tf_required_features.json`    | Required feature flag keys (no values)              |
| `tf_required_secrets.json`     | Required secret keys (no values)                    |

---

## AnsibleBuilder

Generates Ansible-native YAML variable files from a `PlatformArtifactModel`. The files are written into each Ansible provisioner's build directory. The `AnsibleDeployer` auto-discovers them at deploy time and passes them as `--extra-vars @file.yml` arguments.

> **Security note:** `AnsibleBuilder` NEVER writes resolved variable, feature, or secret values. It documents workspace structure and resource configuration so playbooks know what is available — secrets must be injected separately at deploy time.

```python
from strata.builders.ansible_builder import AnsibleBuilder

builder = AnsibleBuilder(verbose=True)

# dry_run with a pre-assembled model (avoids reading platform.json from disk)
if not builder.before_build(deployment_service, work_path, build_path, dry_run=True):
    print("Pre-build failed:", builder.get_errors())
elif not builder.build(
    deployment_service, work_path, build_path,
    dry_run=True,
    platform_model=platform_builder._last_platform_model,
):
    print("Build failed:", builder.get_errors())
```

### Constructor

```python
AnsibleBuilder(verbose: bool = False)
```

### Three-Phase Pipeline

#### `before_build(deployment_service, work_path, build_path, dry_run=False, solution_controller=None)` → `bool`

1. Verifies `deployment_service.is_validated()` — error if not validated
2. If `dry_run=False`: verifies `platform.json` exists in the deployment build path — error if missing (run `PlatformBuilder` first)

#### `build(deployment_service, work_path, build_path, dry_run=False, platform_model=None, solution_controller=None)` → `bool`

1. If `platform_model` is supplied: uses it directly (no disk read)
2. If `platform_model=None`: loads and validates `platform.json` via `PlatformService`
3. Calls `_build_ansible_vars()` to assemble all payloads
4. If `dry_run=True`: logs planned file paths, returns `True` without writing
5. If `dry_run=False`: writes all files to each resolved Ansible provisioner build path

#### `after_build(deployment_service, work_path, build_path, dry_run=False, solution_controller=None)` → `bool`

1. If `dry_run=True`: returns `True` immediately
2. For each resolved Ansible provisioner path: verifies at least one `strata_*.yml` file exists

### Output Files

All files are written to `{provisioner_build_path}/` (one set per Ansible provisioner found in the deployment).

File names use the prefix `strata_` so the deployer can discover them via glob.

| File                     | Top-level YAML key  | Contents                                            |
| ------------------------ | ------------------- | --------------------------------------------------- |
| `strata_workspace.yml`   | `strata_workspace`  | Workspace name, version, environment, labels        |
| `strata_providers.yml`   | `strata_providers`  | Provider configurations keyed by name               |
| `strata_topologies.yml`  | `strata_topologies` | Topology definitions with components and volumes    |
| `strata_resources.yml`   | `strata_resources`  | All resources in a single dict keyed by name        |
| `strata_resx_<type>.yml` | `strata_<type>`     | Resources grouped by `resource_type` (one per type) |
| `strata_modules.yml`     | `strata_modules`    | Module source paths and properties                  |
| `strata_namespaces.yml`  | `strata_namespaces` | Namespace definitions                               |
| `strata_firewalls.yml`   | `strata_firewalls`  | Firewall rules keyed by name                        |
| `strata_dns.yml`         | `strata_dns_zones`  | DNS zone definitions                                |
| `strata_networks.yml`    | `strata_networks`   | Network configurations                              |

### Playbook Variable Access

```yaml
# strata_workspace.yml
- name: Configure deployment
  hosts: all
  vars_files:
    - strata_workspace.yml
  tasks:
    - debug:
        msg: "Deploying {{ strata_workspace.name }} v{{ strata_workspace.version }}"

# strata_resources.yml
- name: Provision resources
  hosts: all
  tasks:
    - name: Iterate all resources
      loop: "{{ strata_resources | dict2items }}"
      debug:
        msg: "Resource {{ item.key }}: {{ item.value.type }}"

# strata_resx_<type>.yml (e.g. strata_objectstorage.yml)
- name: Create object storage buckets
  hosts: all
  tasks:
    - name: Create bucket
      loop: "{{ strata_objectstorage | dict2items }}"
      # ...
```

When `AnsibleDeployer` discovers these files, it passes them automatically — no manual `vars_files` block is needed in the playbook. Variable precedence: files passed via `-e @file.yml` take priority over `vars_files` and inventory vars.

---

## ComposeBuilder

Generates a `docker-compose.yml` file for each namespace that contains at least one module with `spec.type: compose`.

> **Security note:** `ComposeBuilder` NEVER writes resolved secret, variable, or feature values. References are emitted as `${KEY}` substitution tokens and injected by the deployer via a `.env` file at deploy time.

### Output Location

```
{deployment_build_path}/{namespace}/docker-compose.yml
```

One file per namespace. All compose modules in the same namespace are merged into a single file.

### Service Naming

| Condition                     | Service name in compose |
| ----------------------------- | ----------------------- |
| `module.name != service.name` | `{module}-{service}`    |
| `module.name == service.name` | `{service}` (no prefix) |

Entries in `depends_on` are automatically rewritten from their short module-local names to their full prefixed form using the same rule.

#### Cross-Module Dependencies

To depend on a service in another module within the same namespace, use `@module/service` syntax:

| Syntax             | Meaning                                                           |
| ------------------ | ----------------------------------------------------------------- |
| `redis`            | Intra-module: service `redis` in this module                      |
| `@mod_auth/server` | Cross-module: service `server` in module `mod_auth`               |
| `@mod_auth`        | Cross-module shorthand: service whose name equals the module name |

Cross-module refs are validated at build time — the builder checks that the target module exists in the namespace and the service exists in that module. Intra-module refs are validated at module load time.

Example:

```yaml
# Module: mod_web (depends on mod_auth in the same namespace)
spec:
  type: compose
  services:
    - name: website
      image: nginx:alpine
      depends_on:
        - "@mod_auth/server"  # cross-module
        - database            # intra-module
    - name: database
      image: postgres:16
```

Generated output:

```yaml
services:
  mod_web-website:
    image: nginx:alpine
    depends_on:
      - mod_auth-server
      - mod_web-database
  mod_web-database:
    image: postgres:16
```

### Environment Variable Sources

| YAML source field | Value emitted in compose                      |
| ----------------- | --------------------------------------------- |
| `value: "foo"`    | `KEY: foo` (literal string)                   |
| `var: MY_VAR`     | `KEY: ${MY_VAR}` (substituted at deploy time) |
| `secret: MY_SEC`  | `KEY: ${MY_SEC}` (substituted at deploy time) |
| `feature: MY_FLG` | `KEY: ${MY_FLG}` (substituted at deploy time) |

### Volume Conventions

| Mount type          | YAML field                | Compose entry                               | Top-level volume declared?   |
| ------------------- | ------------------------- | ------------------------------------------- | ---------------------------- |
| Named Docker volume | `volume_ref: redis-data`  | `{namespace}_{module}_{volume_ref}:/target` | Yes — `{ns}_{mod}_{ref}: {}` |
| Bind mount          | `source_path: /host/path` | `/host/path:/target`                        | No                           |

### Healthcheck Types

| `type` field | `command` field | Docker Compose `test` emitted                |
| ------------ | --------------- | -------------------------------------------- |
| any          | `["cmd", ...]`  | `[CMD, cmd, ...]`                            |
| `http`       | —               | `[CMD-SHELL, curl -sf {target} \|\| exit 1]` |
| `tcp`        | —               | `[CMD-SHELL, nc -z {target} \|\| exit 1]`    |

### Module YAML Example

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: mymod
spec:
  type: compose
  services:
    - name: redis
      image: redis:7-alpine
      restart: unless-stopped
      environment:
        - key: REDIS_PASSWORD
          secret: REDIS_PASSWORD
      ports:
        - "6379:6379"
      mounts:
        - volume_ref: redis-data
          target_path: /data
      healthcheck:
        type: command
        command: ["redis-cli", "ping"]
        interval: 30s
        timeout: 5s
        retries: 3
    - name: api
      image: myapp:latest
      depends_on: [redis]
      environment:
        - key: REDIS_URL
          value: redis://redis:6379
```

### Generated `docker-compose.yml`

Given the module above in namespace `myns`:

```yaml
services:
  mymod-redis:
    image: redis:7-alpine
    restart: unless-stopped
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD}
    ports:
      - "6379:6379"
    volumes:
      - myns_mymod_redis-data:/data
    healthcheck:
      test: [CMD, redis-cli, ping]
      interval: 30s
      timeout: 5s
      retries: 3
  mymod-api:
    image: myapp:latest
    depends_on:
      - mymod-redis
    environment:
      REDIS_URL: redis://redis:6379
volumes:
  myns_mymod_redis-data: {}
```

### `.env` File and Secret Injection

All `${KEY}` tokens in the generated compose file are resolved by Docker Compose at startup from a `.env` file placed next to `docker-compose.yml`. The deployer is responsible for writing that file — `ComposeBuilder` produces no `.env` output. See the deployer documentation for how secrets and variables are sourced and written at deploy time.

### `configuration:` Escape Hatch

Any keys placed under `configuration:` on a service entry are merged verbatim into the generated compose service block (last-wins). Use this for compose-specific fields that the Strata module model does not natively expose — for example `logging`, `cap_add`, or `sysctls`:

```yaml
spec:
  services:
    - name: api
      image: myapp:latest
      configuration:
        logging:
          driver: json-file
          options:
            max-size: "10m"
        cap_add:
          - NET_ADMIN
```

### Three-Phase Pipeline

#### `before_build` → `bool`

1. Verifies `deployment_service.is_validated()` — error if not validated
2. Verifies `deployment_service.get_workspace_service()` returns a workspace — error if None

#### `build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. Iterates all namespaces from `deployment_service.get_namespace_services()`
2. For each namespace: loads each referenced module, skips modules where `spec.type != compose`
3. Renders services and named volumes into a compose document
4. If `dry_run=True`: logs the planned output path (when verbose), returns `True` without writing
5. If `dry_run=False`: writes `docker-compose.yml` to `{deployment_build_path}/{namespace}/`

#### `after_build` → `bool`

Always returns `True`. A namespace that contains no compose modules is not an error condition.

---

## HelmBuilder

Generates `values.yaml` and `meta.yaml` artifacts for each module with `spec.type: helm`, ready for use with `helm install --values`.

> **Security note:** `HelmBuilder` NEVER writes resolved variable, secret, or feature values. References are emitted as typed `${var:KEY}` / `${secret:KEY}` / `${feature:KEY}` substitution expressions (ADR-0075) — the same syntax `TerraformDeployer` uses for backend config. The deployer resolves them at deploy time: secret-shaped values via `helm upgrade --set-string` (never written to disk), var/feature-only values via a rewritten values file passed with `-f`.

### Output Location

```
{deployment_build_path}/{namespace_name}/{module_name}/values.yaml
{deployment_build_path}/{namespace_name}/{module_name}/meta.yaml
```

Two files per module. Each module with `spec.type: helm` produces its own pair.

### Service Naming

Service keys in `values.yaml` follow the same prefix rule as `ComposeBuilder`:

| Condition                     | Key in `values.yaml`    |
| ----------------------------- | ----------------------- |
| `module.name != service.name` | `{module}-{service}`    |
| `module.name == service.name` | `{service}` (no prefix) |

### Environment Variable Sources

| YAML source field | Value emitted in `values.yaml`                     |
| ----------------- | -------------------------------------------------- |
| `value: "foo"`    | `KEY: foo` (literal string)                        |
| `var: MY_VAR`     | `KEY: ${var:MY_VAR}` (resolved at deploy time)     |
| `secret: MY_SEC`  | `KEY: ${secret:MY_SEC}` (resolved at deploy time)  |
| `feature: MY_FLG` | `KEY: ${feature:MY_FLG}` (resolved at deploy time) |

These typed references may appear anywhere in the rendered values document — not just under an `env:` key — and are cross-checked at build time against declared variables/secrets/features (an undeclared name blocks the build). An unresolved reference also fails loud at deploy time; see ADR-0075.

### PVC Persistence

Mounts with a `storage_class` field generate a `persistence.{mount_name}` block in the service entry. Bind mounts and volume refs are not emitted by `HelmBuilder` — only Kubernetes PVC mounts produce output.

| Mount field     | `persistence` key        |
| --------------- | ------------------------ |
| `name`          | block key (e.g. `media`) |
| `storage_class` | `storageClass`           |
| `access_mode`   | `accessMode`             |
| `storage_size`  | `size`                   |

### `meta.yaml` Contents

| Field         | Source                      | Fallback              |
| ------------- | --------------------------- | --------------------- |
| `releaseName` | `spec.release_name`         | module name           |
| `namespace`   | `spec.kubernetes_namespace` | strata namespace name |

### Module YAML Example

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: authentik
spec:
  type: helm
  release_name: authentik
  kubernetes_namespace: auth
  references:
    secrets: [AUTHENTIK_SECRET_KEY, DB_PASSWORD]
    variables: [AUTHENTIK_VERSION]
  services:
    - name: server
      image: ghcr.io/goauthentik/server:latest
      environment:
        - key: AUTHENTIK_SECRET_KEY
          secret: AUTHENTIK_SECRET_KEY
        - key: VERSION
          var: AUTHENTIK_VERSION
      mounts:
        - name: media
          storage_class: standard
          access_mode: ReadWriteOnce
          storage_size: 5Gi
          target_path: /media
    - name: worker
      image: ghcr.io/goauthentik/server:latest
      command: [worker]
      environment:
        - key: AUTHENTIK_SECRET_KEY
          secret: AUTHENTIK_SECRET_KEY
```

Given the module above in namespace `myns`:

### Generated `myns/authentik/values.yaml`

```yaml
authentik-server:
  env:
    AUTHENTIK_SECRET_KEY: ${AUTHENTIK_SECRET_KEY}
    VERSION: ${AUTHENTIK_VERSION}
  persistence:
    media:
      storageClass: standard
      accessMode: ReadWriteOnce
      size: 5Gi
authentik-worker:
  env:
    AUTHENTIK_SECRET_KEY: ${AUTHENTIK_SECRET_KEY}
```

### Generated `myns/authentik/meta.yaml`

```yaml
releaseName: authentik
namespace: auth
```

### `${KEY}` Injection at Deploy Time

All `${KEY}` tokens in `values.yaml` are not resolved by the builder. At deploy time the deployer substitutes them either via:

- `helm install --set KEY=value ...` flags, or
- a secrets values file passed as `helm install --values secrets.yaml`

`HelmBuilder` produces no secrets output of its own. See the deployer documentation for how variables and secrets are sourced and injected.

### `configuration:` Escape Hatch

Any keys placed under `configuration:` on a service entry are merged verbatim into that service's block in `values.yaml` (last-wins). Use this for chart-specific fields that the Strata module model does not natively expose:

```yaml
spec:
  services:
    - name: server
      image: ghcr.io/goauthentik/server:latest
      configuration:
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
        podAnnotations:
          prometheus.io/scrape: "true"
```

### Three-Phase Pipeline

#### `before_build` → `bool`

1. Verifies `deployment_service.is_validated()` — error if not validated
2. Verifies `deployment_service.get_workspace_service()` returns a workspace — error if None

#### `build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. Iterates all namespaces from `deployment_service.get_namespace_services()`
2. For each namespace: loads each referenced module, skips modules where `spec.type != helm`
3. For each helm module: renders `values.yaml` and `meta.yaml` in memory
4. If `dry_run=True`: logs the planned output paths (when verbose), returns `True` without writing
5. If `dry_run=False`: writes both files to `{deployment_build_path}/{namespace}/{module}/`

#### `after_build` → `bool`

Always returns `True`. A namespace that contains no helm modules is not an error condition.

---

## SbomBuilder

Generates a CycloneDX 1.6 JSON Software Bill of Materials from an existing `platform.json` artifact and writes `sbom.json` to the same deployment build directory.

### Output Location

```
{deployment_build_path}/sbom.json
```

### Report Modes

`--report` selects what `strata build sbom` produces:

| Mode        | Output                                                        | Use for                             |
| ----------- | ------------------------------------------------------------- | ----------------------------------- |
| `cyclonedx` | `sbom.json` (CycloneDX 1.6 JSON) — **default**                | Supply-chain scanning, compliance   |
| `inventory` | Human-readable grouped listing to stdout (or `--output-file`) | Onboarding, quick platform overview |

```bash
# Write sbom.json (default)
strata build sbom -f xyz-deploy-prd.yaml

# Print a human-readable inventory to the terminal
strata build sbom -f xyz-deploy-prd.yaml --report inventory

# Save the inventory to a file
strata build sbom -f xyz-deploy-prd.yaml --report inventory --output-file inventory.txt
```

Both modes run the same collector pipeline — no second pass.

### Collector Pattern

`SbomBuilder` delegates component discovery to pluggable collectors. Each collector implements `BaseSbomCollector` and is responsible for one artifact type. The default set (in execution order):

| Collector                    | Source                                                     | PURL type         | Scan mode |
| ---------------------------- | ---------------------------------------------------------- | ----------------- | --------- |
| `ContainerImageCollector`    | `platform.spec.modules[].services[].image`                 | `pkg:docker/…`    | —         |
| `ComposeImageCollector`      | `docker-compose.yml` service images in the build directory | `pkg:docker/…`    | ✅         |
| `HelmChartCollector`         | Provisioners with `type: helm` (platform model)            | `pkg:helm/…`      | —         |
| `HelmChartFileCollector`     | `Chart.yaml` files on disk (chart + dependencies)          | `pkg:helm/…`      | ✅         |
| `TerraformProviderCollector` | `required_providers {}` blocks in `*.tf` build files       | `pkg:terraform/…` | ✅         |
| `TerraformModuleCollector`   | `module {}` blocks in `*.tf` build files                   | `pkg:terraform/…` | ✅         |
| `AnsibleCollectionCollector` | `requirements.yml` files in the build directory            | `pkg:ansible/…`   | ✅         |
| `DependencyFileCollector`    | Dependency manifests in workspace repos (see below)        | `pkg:pypi/…` etc. | ✅         |

**Scan mode** (`strata build sbom --scan PATH`) runs all collectors against an arbitrary
directory without requiring a strata workspace, deployment file, or `platform.json`.
Model-dependent collectors (`ContainerImageCollector`, `HelmChartCollector`) return
empty results gracefully.  File-based collectors scan the directory tree normally.

Collectors are injectable — pass `collectors=[…]` to the constructor for testing or extension:

```python
from strata.builders.sbom_builder import SbomBuilder
from strata.builders.sbom.image_collector import ContainerImageCollector

builder = SbomBuilder(collectors=[ContainerImageCollector()])
builder.before_build(deployment_service, work_path, build_path)
builder.build(deployment_service, work_path, build_path)
builder.after_build(deployment_service, work_path, build_path)

ref = builder.sbom_reference  # SbomReferenceModel with path, sha256, component_count
```

### Floating Tags

Container images with non-pinned tags (`latest`, `main`, `dev`, etc.) are flagged with a `strata:tag-stability=floating` CycloneDX property. Pinned semver tags and digests do not trigger this.

**Silent by default** — during `strata build run`, floating-tag advisories are suppressed unless `--verbose` is passed or an `sbom_pinned_versions` policy is configured. The component property is always set regardless of verbosity, so policies evaluate correctly when later configured.

To see all collector advisories, either:
- Run `strata build sbom` explicitly, or
- Add `--verbose` to `strata build run`

### Three-Phase Pipeline

#### `before_build`

1. Verifies `deployment_service.is_validated()` — error if not validated
2. Unless `dry_run=True`, verifies `platform.json` exists in the build path — error if missing

#### `build`

1. Runs each collector in order, draining warnings to the logger immediately
2. If `dry_run=True`: logs planned component count, returns `True` without writing
3. If `dry_run=False`: builds CycloneDX BOM, serializes to JSON, writes `sbom.json`, computes SHA-256, stores `SbomReferenceModel` on `self.sbom_reference`

#### `after_build`

Verifies `sbom.json` exists on disk (skipped in dry-run). Returns `False` and adds an error if the file is absent.

### DependencyFileCollector — Scope Control

`DependencyFileCollector` scans workspace repos for dependency manifests
(`requirements*.txt`, `pyproject.toml`, `uv.lock`, `package-lock.json`, `go.sum`).
To keep build times predictable it limits its search:

**Scan paths** — taken from `solution.json → spec.repositories` (the repos registered
in the solution).  If no repositories are registered, it falls back to walking
`work_path`.

**Skip dependency scanning entirely** — pass `--no-deps` to `strata build sbom` to
exclude `DependencyFileCollector` from the run.  Useful for large repos where lockfile
scanning is slow and dependency coverage is not needed for the current build.

```bash
strata build sbom -f xyz-deploy-prd.yaml --no-deps
```

**Ignore patterns** — controlled by `.strata/sbom-ignore.yaml` (optional):

```yaml
# .strata/sbom-ignore.yaml
ignore_paths:
  - "**/node_modules"     # always ignored by default
  - "**/.venv"            # always ignored by default
  - "docs/**"             # example: skip docs folder
ignore_files:
  - "requirements-dev.txt"
  - "requirements-test.txt"
```

Default ignored paths include `**/node_modules`, `**/.venv`, `**/venv`, `**/dist`,
`**/build`, `**/__pycache__`, and `**/.git`.  Entries in `sbom-ignore.yaml` are
**additive** — they extend the defaults rather than replace them.

### CVE Audit (`--audit`)

After generating the SBOM, strata can optionally scan it for known vulnerabilities
using a locally-installed CVE scanner. No network calls to external APIs — the
scanner runs locally against the generated `sbom.json`.

**Prerequisites:** install [Trivy](https://trivy.dev) (preferred) or
[Grype](https://github.com/anchore/grype) in your PATH. Strata auto-detects
whichever is available (Trivy takes priority if both exist).

```bash
# Advisory mode — prints findings, never fails the build
strata build sbom -f xyz-deploy-prd.yaml --audit

# CI gate — fail (exit code 3) if HIGH or CRITICAL vulnerabilities exist
strata build sbom --scan . --audit --fail-on HIGH

# Only report CRITICAL vulnerabilities
strata build sbom --scan . --audit --severity CRITICAL --fail-on CRITICAL
```

| Flag               | Default  | Description                                                                 |
| ------------------ | -------- | --------------------------------------------------------------------------- |
| `--audit`          | off      | Enable CVE scanning after SBOM generation                                   |
| `--severity LEVEL` | `MEDIUM` | Minimum severity to include: `CRITICAL`\|`HIGH`\|`MEDIUM`\|`LOW`\|`UNKNOWN` |
| `--fail-on LEVEL`  | *(none)* | Exit code 3 if findings at this severity or above exist                     |

**Console output** shows a severity summary table and the top 10 findings.
**NDJSON output** (`--output ndjson`) emits each finding as a `data` event with
an `audit_finding` payload.  **JSON output** includes an `audit` key with
severity counts.

When no scanner is found in PATH, `--audit` prints a warning and continues — it
never blocks a build due to missing tooling.

### Built-in lockfile formats

| File pattern         | Language / Ecosystem | Parser                    |
| -------------------- | -------------------- | ------------------------- |
| `requirements*.txt`  | Python / `pypi`      | `RequirementsTxtParser`   |
| `pyproject.toml`     | Python / `pypi`      | `PyprojectTomlParser`     |
| `uv.lock`            | Python / `pypi`      | `UvLockParser`            |
| `package-lock.json`  | Node.js / `npm`      | `PackageLockJsonParser`   |
| `go.sum`             | Go / `golang`        | `GoSumParser`             |
| `packages.lock.json` | .NET / `nuget`       | `NugetPackagesLockParser` |
| `packages.config`    | .NET / `nuget`       | `PackagesConfigParser`    |
| `pom.xml`            | Java / `maven`       | `MavenPomParser`          |
| `gradle.lockfile`    | Java / `maven`       | `GradleLockParser`        |
| `Gemfile.lock`       | Ruby / `gem`         | `GemfileLockParser`       |
| `Cargo.lock`         | Rust / `cargo`       | `CargoLockParser`         |
| `composer.lock`      | PHP / `packagist`    | `ComposerLockParser`      |

Each parser lives in `strata.builders.sbom.lockfile_parsers.<language>` and is
exported from the package `__init__.py`.

Additional formats are added via workspace lockfile parser plugins — see below.

### Extending the SBOM

#### Workspace plugins

Teams can add collectors and lockfile parsers without forking strata.  There are
two ways to add a lockfile parser:

**Option A — Drop folder (zero config):** place any `.py` file in
`.strata/lockfile_parsers/` and it is imported automatically.  No YAML entry
needed.  Files starting with `_` are skipped.

**Option B — Explicit config:** declare the plugin in `.strata/collectors.yaml`.
This is required for full `BaseSbomCollector` plugins, and is an alternative for
lockfile parsers when you want to store them outside the drop folder.

Plugin declarations in `.strata/collectors.yaml`:

```yaml
# .strata/collectors.yaml
collectors:
  # A full BaseSbomCollector subclass — appended after the built-ins
  - name: cargo
    path: .strata/plugins/cargo_collector.py
    class: CargoCollector
    type: collector

  # A LockfileParser subclass — auto-registered into DependencyFileCollector
  - name: cargo-lock
    path: .strata/plugins/cargo_lockfile_parser.py
    type: lockfile_parser   # class is optional — __init_subclass__ handles it
```

`path` is relative to the workspace root.  Use `module` instead of `path` to load
from an installed package:

```yaml
  - name: cargo-lock
    module: my_company.strata_plugins.cargo
    type: lockfile_parser
```

`strata sln init` creates `.strata/plugins/` with two annotated starter templates,
plus a commented-out `collectors.yaml` stub and an `sbom-ignore.yaml` stub:

- `my_collector.py` — skeleton for a `BaseSbomCollector` subclass
- `my_lockfile_parser.py` — skeleton for a `LockfileParser` subclass
- `collectors.yaml` — plugin declarations (all examples commented out by default)
- `sbom-ignore.yaml` — ignore rules for `DependencyFileCollector`

#### Writing a collector plugin

A collector plugin reads from any source (files, APIs, environment) and returns
`SbomComponentModel` instances:

```python
# .strata/plugins/my_collector.py
from pathlib import Path
from typing import List
from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.models.sbom_model import SbomComponentModel

class CargoCollector(BaseSbomCollector):
    def get_collector_name(self) -> str:
        return "cargo"

    def collect(
        self,
        platform,           # PlatformArtifactModel
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        components = []
        # ... scan files, call APIs, etc.
        # Use self._add_warning(msg) for non-fatal issues.
        components.append(SbomComponentModel(
            name="serde",
            version="1.0.193",
            purl="pkg:cargo/serde@1.0.193",
            source_collector=self.get_collector_name(),
        ))
        return components
```

Then declare it in `.strata/collectors.yaml` with `type: collector`.

#### Writing a lockfile parser plugin

A lockfile parser plugin is even simpler — it reads one file format and returns
`(name, version)` pairs.  `DependencyFileCollector` handles purl construction,
deduplication, and `SbomComponentModel` creation:

```python
# .strata/plugins/cargo_lockfile_parser.py
import tomllib
from pathlib import Path
from typing import List
from strata.builders.sbom.lockfile_parsers import LockfileParser, RawDependency

# Defining this class is all that's needed.
# __init_subclass__ fires on class body execution and calls
# DEFAULT_REGISTRY.register(CargoLockParser()) automatically.
class CargoLockParser(LockfileParser):
    @property
    def ecosystem(self) -> str:
        return "cargo"

    def filename_patterns(self) -> List[str]:
        return ["Cargo.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return [
            RawDependency(name=pkg["name"], version=pkg.get("version"))
            for pkg in data.get("package", [])
        ]
```

**Option A — Drop folder (zero config):** save the file as
`.strata/lockfile_parsers/cargo.py` and it is imported automatically on the next
build.  No YAML entry needed.

**Option B — Explicit config:** declare it in `.strata/collectors.yaml`:

```yaml
collectors:
  - name: cargo-lock
    path: .strata/plugins/cargo_lockfile_parser.py
    type: lockfile_parser   # class is optional
```

#### Test isolation

Because `LockfileParser.__init_subclass__` fires at class-definition time into the
module-level `DEFAULT_REGISTRY`, test parsers **must** use `register=False` to avoid
polluting the registry across test runs:

```python
class FakeParser(LockfileParser, register=False):
    ...
```

Alternatively inject a fresh registry:

```python
from strata.builders.sbom.lockfile_parsers import LockfileParserRegistry
from strata.builders.sbom.deps_collector import DependencyFileCollector

registry = LockfileParserRegistry()
registry.register(MyTestParser())
collector = DependencyFileCollector(registry=registry)
```

---

## Typical Build Sequence

```python
from pathlib import Path
from strata.builders.strata_builder import PlatformBuilder
from strata.builders.terraform_builder import TerraformBuilder

work_path = Path("/workspace")
build_path = Path("/workspace/.strata/build/")

# Step 1: Build the platform model
platform_builder = PlatformBuilder(verbose=True, configuration_service=config_svc)
platform_builder.before_build(deployment_svc, work_path, build_path)
platform_builder.build(deployment_svc, work_path, build_path, dry_run=False)
platform_builder.after_build(deployment_svc, work_path, build_path)

# Step 2: Build Terraform artifacts using the in-memory model
tf_builder = TerraformBuilder(verbose=True)
tf_builder.before_build(deployment_svc, work_path, build_path, dry_run=False)
tf_builder.build(
    deployment_svc, work_path, build_path,
    dry_run=False,
    platform_model=platform_builder._last_platform_model,
)
tf_builder.after_build(deployment_svc, work_path, build_path)

# Check for errors
for err in platform_builder.get_errors() + tf_builder.get_errors():
    print("ERROR:", err)
```

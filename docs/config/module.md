# Module Configuration

Defines a deployable application unit within a namespace. A module represents one logical application — which may consist of multiple containers — and declares its source, deployer type, services, lifecycle hooks, and runtime references.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: <module_name>           # Required: ^[a-z][a-z0-9_-]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
    category: <category>        # proxy | database | cache | identity | application | ...
spec:
  type: <deployer_type>         # compose | helm | argocd | script
  source: {}                    # Required: git-based or chart-based (see Source)
  release_name: <name>          # Optional: Helm release / ArgoCD app name (default: module name)
  kubernetes_namespace: <ns>    # Optional: K8s namespace (default: strata namespace name)
  references:                   # Optional: keys resolved from environment stores
    variables: []
    secrets: []
    features: []
  services: []                  # Optional: container / sub-chart definitions
  lifecycle: {}                 # Optional: deployment lifecycle hooks
  properties:                   # Optional: module-level metadata
    endpoints: []
    checks: []
    mounts: []
  configuration: {}             # Optional: deployer-specific escape hatch
```

## Source

Two mutually exclusive modes. Use **git-based** when the source is a repository; use **chart-based** for Helm chart registries.

### Git-based source

```yaml
source:
  repository: platform-modules  # PlatformName — from configuration repositories
  source_path: services/traefik  # relative path in repo
  target_path: modules/traefik   # optional: build output path
```

### Chart-based source (Helm / ArgoCD)

```yaml
source:
  chart_name: authentik                        # chart name in the registry
  chart_version: "2024.12.0"                   # optional: pin version (latest if omitted)
  chart_repository: https://charts.goauthentik.io  # registry URL or OCI reference
  # OCI example: oci://ghcr.io/org/helm-charts
```

## Deployer Types

| Type      | Description                                 | Source mode              |
| --------- | ------------------------------------------- | ------------------------ |
| `compose` | Docker Compose — deployed via SSH           | git-based                |
| `helm`    | Helm chart — deployed via `helm` CLI        | git-based or chart-based |
| `argocd`  | ArgoCD Application — managed in gitops repo | chart-based or git-based |
| `script`  | Custom deployment script                    | git-based                |

## Services

The `services` list defines the containers (compose) or sub-chart components (helm) that make up the module. When absent, the module uses the single-service `properties` shape for backward compatibility.

> **Tip:** Service images defined here are defaults. Use [environment overrides](environment.md#module-overrides) to pin different image versions per environment without modifying the module file.

```yaml
services:
  - name: server                               # service identifier
    image: ghcr.io/goauthentik/server:2024.12  # container image (omit for helm charts)
    command: [server]                          # optional: override entrypoint
    restart: unless-stopped                    # compose only
    ports:
      - "9000:9000"                            # compose only; ignored by helm/argocd
    environment:
      - key: AUTHENTIK_SECRET_KEY
        secret: AUTHENTIK_SECRET_KEY           # resolved from references.secrets
      - key: TZ
        value: Europe/Brussels                 # literal value
      - key: APP_VERSION
        var: APP_VERSION                       # resolved from references.variables
      - key: ENABLE_METRICS
        feature: ENABLE_METRICS               # resolved to "true"/"false"
    mounts:
      - name: pgdata
        target_path: /var/lib/postgresql/data
        volume_ref: data                       # compose: references topology volume
    depends_on: [postgresql, redis]            # intra-module: builder prefixes names
    # depends_on: ["@mod_auth/server"]         # cross-module: @module/service syntax
    healthcheck:
      type: http
      target: http://localhost:9000/-/health/live/
      interval: "30s"
      timeout: "5s"
      retries: 3
    configuration: {}                          # deployer-specific overrides merged verbatim
```

### Environment variable sources

Each `environment` entry sets exactly one of:

| Field     | Description                     | Artifact output                                                                                                                                    |
| --------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `value`   | Literal string                  | Written directly                                                                                                                                   |
| `var`     | Key from `references.variables` | Compose: `${KEY}` via `.env` at deploy time. Helm: `${var:KEY}` (ADR-0075), resolved into a rewritten values file at deploy time.                  |
| `secret`  | Key from `references.secrets`   | Compose: `${KEY}` via `.env` at deploy time. Helm: `${secret:KEY}` (ADR-0075), resolved via `--set-string` at deploy time — never written to disk. |
| `feature` | Key from `references.features`  | Resolved to `"true"` or `"false"`. Helm emits `${feature:KEY}` (ADR-0075).                                                                         |

### Service naming in compose output

The builder prefixes service names with the module name to avoid collisions across modules:

```
Module: authentik  + service: redis   →  compose key: authentik-redis
Module: caddy      + service: caddy   →  compose key: caddy  (prefix skipped when names match)
```

`depends_on` entries are rewritten automatically to the prefixed form.

### Mounts

```yaml
mounts:
  # Compose: reference a named volume from workspace topology
  - name: data
    target_path: /var/lib/postgresql/data
    volume_ref: data                # references WorkspaceVolumeModel.name

  # Compose: bind mount
  - name: config
    type: bind
    source_path: ./Caddyfile
    target_path: /etc/caddy/Caddyfile

  # Helm / K8s: PersistentVolumeClaim
  - name: pgdata
    target_path: /var/lib/postgresql/data
    storage_class: standard
    storage_size: 10Gi
    access_mode: ReadWriteOnce     # optional, defaults to ReadWriteOnce
```

`volume_ref` and `storage_class` are mutually exclusive.

## References

Declares the variable, secret, and feature keys this module requires from the environment configuration. Actual values and store backends are defined at environment/workspace level.

```yaml
references:
  variables:
    - SUBDOMAIN_AUTH
    - DOMAIN_PRIMARY
  secrets:
    - AUTHENTIK_SECRET_KEY
    - AUTHENTIK_DB_PASSWORD
  features:
    - ENABLE_METRICS
```

## Lifecycle Hooks

Optional hooks executed at specific deployment phases.

```yaml
lifecycle:
  service_start_before:
    scripts: ["scripts/pre-start.sh"]
  service_start_after:
    scripts: ["scripts/post-start.sh"]
  deploy_provision:
    scripts: ["scripts/provision.sh"]
  deploy_configure:
    scripts: ["scripts/configure.sh"]
  deploy_health:
    scripts: ["scripts/healthcheck.sh"]
  deploy_destroy_before:
    scripts: ["scripts/pre-destroy.sh"]
  deploy_destroy_after:
    scripts: ["scripts/post-destroy.sh"]
```

## Properties

Module-level metadata — not per-service. Endpoints document external-facing access points; these are informational and used by the platform artifact.

```yaml
properties:
  endpoints:
    - name: web
      label: Authentik SSO
      url: https://auth.example.com
      port: 9443
      type: https
      protocol: tcp
  checks:
    - name: liveness
      type: http
      target: http://localhost:9000/-/health/live/
      interval: "30s"
      timeout: "5s"
      retries: 3
```

## Examples

### Multi-container compose module (Authentik)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: authentik
  labels:
    category: identity
spec:
  type: compose
  source:
    repository: haven-modules
    source_path: services/authentik

  references:
    variables: [SUBDOMAIN_AUTH, DOMAIN_PRIMARY]
    secrets: [AUTHENTIK_SECRET_KEY, AUTHENTIK_DB_PASSWORD]

  services:
    - name: server
      image: ghcr.io/goauthentik/server:2024.12
      ports: ["9000:9000"]
      environment:
        - key: AUTHENTIK_SECRET_KEY
          secret: AUTHENTIK_SECRET_KEY
        - key: AUTHENTIK_POSTGRESQL__HOST
          value: authentik-postgresql
      depends_on: [postgresql, redis]
      healthcheck:
        type: http
        target: http://localhost:9000/-/health/live/
        interval: "30s"
        timeout: "5s"
        retries: 3
      restart: unless-stopped

    - name: worker
      image: ghcr.io/goauthentik/server:2024.12
      command: [worker]
      environment:
        - key: AUTHENTIK_SECRET_KEY
          secret: AUTHENTIK_SECRET_KEY
      depends_on: [postgresql, redis]
      restart: unless-stopped

    - name: postgresql
      image: postgres:16-alpine
      environment:
        - key: POSTGRES_PASSWORD
          secret: AUTHENTIK_DB_PASSWORD
      mounts:
        - name: pgdata
          target_path: /var/lib/postgresql/data
          volume_ref: data
      restart: unless-stopped

    - name: redis
      image: redis:alpine
      command: ["--save", "60", "1"]
      restart: unless-stopped

  properties:
    endpoints:
      - name: web
        label: Authentik SSO
        url: https://${SUBDOMAIN_AUTH}.${DOMAIN_PRIMARY}
        port: 9443
```

### Single-container compose module (Caddy)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: caddy
  labels:
    category: proxy
spec:
  type: compose
  source:
    repository: haven-modules
    source_path: services/caddy

  references:
    variables: [DOMAIN_PRIMARY]

  services:
    - name: caddy
      image: caddy:2-alpine
      ports: ["80:80", "443:443"]
      mounts:
        - name: caddyfile
          type: bind
          source_path: ./Caddyfile
          target_path: /etc/caddy/Caddyfile
        - name: caddy_data
          target_path: /data
          volume_ref: data
      restart: unless-stopped
```

### Helm chart module

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: traefik
  labels:
    category: proxy
spec:
  type: helm
  source:
    chart_name: traefik
    chart_version: "26.0.0"
    chart_repository: https://traefik.github.io/charts

  kubernetes_namespace: ingress

  services:
    - name: traefik
      configuration:
        ingressClass:
          enabled: true
        ports:
          web:
            port: 80
```

## Module Categories

| Category      | Purpose                        | Examples                      |
| ------------- | ------------------------------ | ----------------------------- |
| `proxy`       | Reverse proxies/load balancers | Traefik, Caddy, Nginx         |
| `identity`    | Authentication/SSO             | Authentik, Keycloak           |
| `database`    | Data storage                   | PostgreSQL, MySQL, MongoDB    |
| `cache`       | Caching                        | Redis, Memcached              |
| `queue`       | Message queues                 | RabbitMQ, Kafka, NATS         |
| `application` | Business apps                  | APIs, web apps, services      |
| `monitoring`  | Observability                  | Prometheus, Grafana, Loki     |
| `security`    | Security services              | Vaultwarden, Infisical, Vault |
| `storage`     | Storage solutions              | MinIO, GlusterFS              |

## Namespace Integration

Modules are referenced from a namespace by name and file path. The order of modules in the namespace list controls deployment sequence.

```yaml
# namespace.yaml
spec:
  modules:
    - name: caddy        # deployed first
      file: modules/mod-caddy.yaml
    - name: authentik    # deployed second
      file: modules/mod-authentik.yaml
    - name: vaultwarden  # deployed third
      file: modules/mod-vaultwarden.yaml
```

## Build Output

For `type: compose`, the builder merges all modules in a namespace into one `docker-compose.yml`:

```
.strata/build/<namespace>/docker-compose.yml
```

For `type: helm`, the builder produces per-module values:

```
.strata/build/<namespace>/<module>/values.yaml
.strata/build/<namespace>/<module>/Chart.yaml
```

## Validation

- Name: `^[a-z][a-z0-9_-]*$`, max 64 characters
- Service names must be unique within the module
- `environment` entries must set exactly one of `value`, `var`, `secret`, `feature`
- `volume_ref` and `storage_class` are mutually exclusive on mounts
- `storage_size` is required when `storage_class` is set
- `source`: either git-based (`repository` + `source_path`) or chart-based (`chart_repository` + `chart_name`), not both

## Troubleshooting

**Duplicate service names:** Each service in a module must have a unique `name`.  
**Environment var missing source:** Every `environment` entry needs exactly one of `value`, `var`, `secret`, `feature`.  
**Mount conflict:** `volume_ref` and `storage_class` cannot both be set on the same mount.  
**Source mode conflict:** Do not mix `repository`/`source_path` with `chart_repository`/`chart_name`.  
**Secret not resolved:** Ensure the key is listed in `references.secrets` and the environment store provides it.  
**Service not found in depends_on:** `depends_on` entries must reference service names within the same module (short names — builder handles prefixing).


## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: <module_name> # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
    category: <category> # proxy, database, cache, etc.
spec:
  lifecycle: # Optional: module-specific hooks
    bootstrap: {} # Before module deployment
    provision: {} # During module provisioning
    configure: {} # After module deployment
    health: {} # After configuration
    protect: {} # After successful deployment
    destroy: {} # During module teardown
  properties:
    source: # Required: deployment source
      type: <source_type> # local, gitops, image, script
      repository: <repository>
      reference: <reference>
      source_path: <source_path>
      deploy_path: <deploy_path>
```

## Source Types

**Local** - Workspace configuration:

```yaml
source:
  type: local
  repository: /
  reference: /
  source_path: services/traefik
  deploy_path: modules/traefik
```

_Use for: workspace services, custom apps, infrastructure services_

**GitOps** - External Git repository:

```yaml
source:
  type: gitops
  repository: https://github.com/org/repo.git
  reference: main
  source_path: deployments/app
  deploy_path: modules/app
```

_Use for: shared configs, external services, multi-workspace deployments_

**Image** - Container image:

```yaml
source:
  type: image
  repository: docker.io/traefik/traefik
  reference: v2.10
  source_path: /
  deploy_path: modules/traefik
```

_Use for: pre-packaged apps, third-party services, standard containers_

**Script** - Custom deployment script:

```yaml
source:
  type: script
  repository: /
  reference: v1.0.0
  source_path: scripts/deploy-custom.sh
  deploy_path: modules/custom
```

_Use for: complex deployment logic, custom procedures, legacy apps_

## Examples

**Traefik Proxy:**

```yaml
meta:
  name: traefik
  labels:
    version: "1.0.0"
    category: proxy
spec:
  properties:
    source:
      type: local
      repository: /
      reference: /
      source_path: services/traefik
      deploy_path: modules/traefik
```

**PostgreSQL with Lifecycle:**

```yaml
meta:
  name: postgres
  labels:
    version: "15.0.0"
    category: database
spec:
  lifecycle:
    bootstrap:
      scripts:
        - file: scripts/postgres/prepare-storage.sh
    configure:
      scripts:
        - file: scripts/postgres/init-db.sh
        - file: scripts/postgres/run-migrations.sh
    health:
      scripts:
        - file: scripts/postgres/health-check.sh
    protect:
      scripts:
        - file: scripts/postgres/backup.sh
  properties:
    source:
      type: image
      repository: docker.io/library/postgres
      reference: "15-alpine"
      source_path: /
      deploy_path: modules/postgres
```

**GitOps Application:**

```yaml
meta:
  name: api_service
  labels:
    version: "2.3.1"
    category: application
spec:
  lifecycle:
    configure:
      scripts:
        - file: scripts/api/setup-env.sh
    health:
      scripts:
        - file: scripts/api/smoke-test.sh
  properties:
    source:
      type: gitops
      repository: https://github.com/org/api-service.git
      reference: v2.3.1
      source_path: deploy/kubernetes
      deploy_path: modules/api
```

**Container Image:**

```yaml
meta:
  name: redis
  labels:
    version: "7.0.0"
    category: cache
spec:
  properties:
    source:
      type: image
      repository: docker.io/library/redis
      reference: "7.0-alpine"
      source_path: /
      deploy_path: modules/redis
```

## Module Categories

| Category      | Purpose                        | Examples                   |
| ------------- | ------------------------------ | -------------------------- |
| `proxy`       | Reverse proxies/load balancers | Traefik, Nginx, HAProxy    |
| `database`    | Data storage                   | PostgreSQL, MySQL, MongoDB |
| `cache`       | Caching                        | Redis, Memcached           |
| `queue`       | Message queues                 | RabbitMQ, Kafka, NATS      |
| `application` | Business apps                  | APIs, web apps, services   |
| `monitoring`  | Observability                  | Prometheus, Grafana, Loki  |
| `security`    | Security services              | Vault, cert-manager        |
| `storage`     | Storage solutions              | MinIO, GlusterFS           |

## Path Resolution

**source_path** - Module source location:

- **Local**: Relative to workspace root (`services/traefik`)
- **GitOps**: Relative to repository root (`deploy/kubernetes`)
- **Image**: Container path (typically `/`)
- **Script**: Path to deployment script

**deploy_path** - Deployment artifacts location:

- Relative to build directory
- Platform generates deployment files here
- Example: `modules/traefik` → `build/modules/traefik`

## Namespace Integration

```yaml
# namespace.yaml
spec:
  modules:
    - name: traefik
      file: config/modules/traefik.yaml
    - name: postgres
      file: config/modules/postgres.yaml
```

## Module Patterns

**Stateless:** Image-based, no lifecycle hooks  
**Stateful:** Lifecycle hooks for bootstrap, configure, protect  
**System:** Local source, critical system services

## Best Practices

- **Naming:** Match service name (`traefik`, `postgres`, `redis`)
- **Version labels:** Track versions for rollback
- **Categories:** Organize modules consistently
- **Idempotent scripts:** Safe to run multiple times
- **Error handling:** Include in all lifecycle scripts
- **Source type:** Choose based on deployment method
- **Paths:** Consistent patterns across modules
- **Documentation:** Clear descriptions in annotations
- **Script ordering:** Number scripts for execution control
- **Dependencies:** Document in annotations if needed

## Validation

Platform validates:

- Valid names (lowercase, alphanumeric, underscores)
- Required fields (name, source type, paths)
- Source type matches options
- Script files exist and executable
- Valid path formats
- No conflicting module names

## Troubleshooting

**Module not found:** Verify file path in namespace config, check file exists in `config/modules/`, ensure name matches reference  
**Source path invalid:** Check path exists (local/script), verify URL (gitops), confirm image exists (image), validate format  
**Lifecycle script failed:** Check executable permissions, verify path, review output, ensure dependencies available  
**Deploy path conflicts:** Ensure unique deploy_path per module, check for overlapping directories  
**Version mismatch:** Verify image tag exists, check Git reference exists, confirm version label accuracy

## Dependencies

Control module order in namespace:

```yaml
spec:
  modules:
    - name: database # First
    - name: cache # Second
    - name: backend # Third (depends on database + cache)
    - name: frontend # Last (depends on backend)
```

Document external dependencies in annotations.
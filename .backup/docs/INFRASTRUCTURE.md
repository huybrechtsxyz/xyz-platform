# Infrastructure Overview

## Infrastructure Provisioning

The infrastructure for this self-hosted platform is created and maintained using **Terraform**, an infrastructure-as-code tool that enables declarative provisioning and management of cloud resources.

- **Supported Provider:**  
  Currently, only **Kamatera** cloud platform is supported for infrastructure deployment.  
  Kamatera supports only servers (CPU, RAM, disks) and network resources.

- **HCP Terraform:**
  Cloud environment for storing the state for Terraform.

- **Workspace Configuration:**  
  Each deployment workspace is described by a JSON configuration file located in the `/deploy/workspaces` directory. The file is named using the pattern `{workspaceid}.ws.json`.

## What is a Workspace?

A workspace is an instance of the full cloud environment. It defines all infrastructure components, network topologies, and storage layouts required to deploy, operate, and manage your services. Workspaces are defined at provisioning and deployment. Workspaces are configured using the workspace configuration file.

**Composition:**

- A workspace is made up of a set of servers declared in the configuration file.
- Each server has a defined role (e.g., manager, infra, worker) and disk layout (OS disks, data disks).
- Servers are logically organized to support different service types, storage requirements, and workloads.

**Internal Networking:**

- All servers are automatically connected to each other via a private internal VLAN on 10.0.0.0.
- This internal network allows services to communicate securely without relying on public internet routing.

**Public IP Addresses:**

- Kamatera requires each server to be assigned a public IP address (this is a provider constraint).
- However, for security and operational consistency, **only the manager node’s public IP** should be used for administrative access.
- Other servers should be accessed internally whenever possible.

**Purpose:**

- A workspace represents a self-contained environment where all platform services run together.
- You can think of it as a mini-cloud, fully owned and operated by the owner, encapsulating:
  - Compute resources
  - Networking
  - Persistent storage
  - Service configurations
  - Security policies

## Workspace Configuration File Structure

The workspace JSON file defines the overall deployment environment, including service paths and server specifications. The current deployment workspace is designed to host and run various services and applications in a self-managed environment. Here’s a breakdown of its main sections with the current example.

### Workspace Overview: deploy

Metadata about the workspace such as its name, version, and description.

Current setup:

- **Name:** Platform Workspace  
- **Version:** 1.0.0  
- **Description:** Workspace deployment configuration for the shared Platform.

### Workspace Server Roles: roles

Server roles are named templates that define the hardware and cost parameters of a machine. Each role specifies how the machine is implemented: CPU type and cores, RAM size, and estimated cost per unit (monthly)  
Roles allow us to standardize instance configurations, dynamically create infrastructure, and estimate total costs before deployment.

| Attribute   | Type     | Description                                                 |
| ----------- | -------- | ----------------------------------------------------------- |
| `cpu_type`  | `string` | The CPU family/type to use for the server (e.g., "A", "B")  |
| `cpu_cores` | `number` | Number of CPU cores to assign                               |
| `ram_mb`    | `number` | Memory in megabytes                                         |
| `unit_cost` | `number` | Estimated monthly or hourly cost per server (for budgeting) |

```json
{
  "roles": {
    "manager": {
      "cpu_type": "A",
      "cpu_cores": 1,
      "ram_mb": 1024,
      "unit_cost": 5.00
    },
    "infra": {
      "cpu_type": "A",
      "cpu_cores": 1,
      "ram_mb": 4096,
      "unit_cost": 11.00
    },
    "worker": {
      "cpu_type": "A",
      "cpu_cores": 1,
      "ram_mb": 2048,
      "unit_cost": 6.00
    }
  }
}

```

### Workspace Paths Configuration: paths

Specifies the types of data storage used by services and their corresponding directory paths, using template variables for dynamic substitution.

> ⚠️ If new types of paths are required by a service, they **must be explicitly added here**. This list defines all valid mount types available across the infrastructure.

Each service uses standardized directory paths for different data types. The type (config, data, docs) are used in the different configuration scripts for automation purposes!

**The TYPE of paths is limited to the list below and can't be changed!**

| **Type** | **Path Template** | **Purpose**                                                       |
| -------- |-----------------| --------------------------------------------------------------------|
| `config` | `/etc/app`      | Holds configuration files such as `.env`, `.json`, `.yaml`, scripts |
| `docs`   | `/opt/app/`     | Stores platform and service documentation                           |
| `data`   | `/var/data/app` | Stores service-specific persistent runtime data                     |
| `logs`   | `/var/logs/app` | Stores logs from application runtime                                |
| `serve`  | `/srv/app`      | Directory where the application serves or runs from                 |

These paths are mounted onto servers' disks according to the mount configuration described below. Each path is added to a cluster volume maintained by GlusterFS.GlusterFS is an open-source, distributed file system that lets you pool storage across multiple servers into a single network volume.

- Replicated volumes: Keep identical copies of files on multiple nodes (for redundancy).
- Distributed volumes: Spread files across nodes to scale storage capacity.
- Network-based: All servers see the same filesystem.
- Resilient: Survives node failures if replication is configured.
- Flexible: You can grow storage by adding more servers or disks.

Each service path has a standardized cluster behaviour:

| Type | Description |
| -----|-------------|
| replicated | Replicated. Data is mirrored (copied) across multiple nodes. If one node fails, data is still available from another. High availability, but uses more storage. |
| distributed | Distributed. Data is spread across nodes, not copied. Stores different chunks on different servers. Good for scalability, not redundancy. |
| local | Local. Not managed by GlusterFS — data stays only on the local disk of one server. No syncing, no redundancy. |

Example for standard layout:

```json
"paths": [
  { "type": "config", "path": "/etc/app"     , "volume": "replicated" },
  { "type": "data",   "path": "/var/data/app", "volume": "local" },
  { "type": "logs",   "path": "/var/logs/app", "volume": "local" },
  { "type": "serve",  "path": "/srv/app"     , "volume": "distributed" }
],
```

### Workspace Server Configuration: servers

An array of server definitions describing the nodes within the workspace. Each server entry contains:

- `id`:
  - Unique identifier for the server.
  - Follows naming: srv-{role}-{instance}-{random4}
- `role`:
  - The server's role (e.g., manager, infra, worker).
  - **Manager node:** Typically responsible for orchestrating and managing workloads or cluster control tasks.
  - **Infra nodes:** Provide infrastructure services for other services.
  - **Worker nodes:** Handle execution of application workloads or services.
  - Other types of nodes can be added.
- `labels`:
  - Optional tags for additional metadata (e.g., services running on that server).
- `disks`:
  - An array of attached storage disks with their sizes (in GB) and labels.
  - Each disk is mounted under `/mnt/data{disk}/app` where `${disk}` corresponds to the disk index (1 or 2).
- `mountpoint`:
  - The base path where disks are mounted, supporting disk number substitution.
  - The service data types (`config`, `data`, `logs`, `serve`) are mapped to specific disks based on the `mounts` array to separate storage concerns.
  - **Disk Device Mapping:** Disk indices (`disk 1`, `disk 2`) are assumed to map to actual system devices in order: `disk 1` → `/dev/sda`, `disk 2` → `/dev/sdb`, etc.
  - **Mountpoint Template:** The `${disk}` variable in the mount point path (e.g., `/mnt/data${disk}/app`) dynamically maps each data type to the correct physical disk using the `mounts` array.
- `mounts`:
  - Defines which disk is used for each type of data path (`config`, `data`, `logs`, `serve`).
  - **Mounts Purpose:** The `mounts` section for each server allows individual data types (e.g., config, data, logs, serve) to be written to distinct physical disks, providing separation of concerns and better disk utilization or backup strategies.

The platform consists of multiple server nodes with different roles and storage configurations:

| Server ID  | Role    | Labels            | Disks                                | Mount Point Template        | Mounts (Data type → Disk)               |
|------------|---------|-------------------|------------------------------------|----------------------------|----------------------------------------|
| manager-1  | manager | —                 | 1 × 20 GB (manager-1-os)           | `/mnt/data${disk}/app`      | config → disk 1, data → disk 1, logs → disk 1, serve → disk 1 |
| infra-1    | infra   | -                | 1 × 20 GB (infra-1-os), 1 × 40 GB (infra-1-data) | `/mnt/data${disk}/app` | config → disk 2, data → disk 2, logs → disk 2, serve → disk 2 |
| infra-2    | infra   | —                 | 1 × 20 GB (infra-2-os), 1 × 40 GB (infra-2-data) | `/mnt/data${disk}/app` | config → disk 2, data → disk 2, logs → disk 2, serve → disk 2 |
| worker-1   | worker  | —                 | 1 × 20 GB (worker-1-os)            | `/mnt/data${disk}/app`      | config → disk 1, data → disk 1, logs → disk 1, serve → disk 1 |
| worker-2   | worker  | —                 | 1 × 20 GB (worker-2-os)            | `/mnt/data${disk}/app`      | config → disk 1, data → disk 1, logs → disk 1, serve → disk 1 |

```json
{
  {
      "id": "worker-1",
      "role": "worker",
      "labels": [],
      "disks": [
        { "size": 20, "label": "worker-1-os" }
      ],
      "mountpoint": "/mnt/data${disk}/app",
      "mounts": [
        { "type": "config", "disk": 1 },
        { "type": "data", "disk": 1 },
        { "type": "logs", "disk": 1 },
        { "type": "serve", "disk": 1 }
      ]
    }
}
```

This structured configuration allows Terraform scripts to automate the provisioning of servers and storage according to predefined roles and capacity requirements, ensuring consistent and repeatable infrastructure deployments.

This setup allows clear separation of roles and data on distinct nodes with explicit storage configurations, ensuring a scalable and maintainable platform deployment.

### Workspace Secrets

The following secrets are required for the workspace to function:

| Secret Name              | Description                                                                                  |
| ------------------------ | -------------------------------------------------------------------------------------------- |
| `TERRAFORM_API_TOKEN`    | Terraform Cloud or HCP API token. Used to authenticate when running Terraform plans/applies. |
| `KAMATERA_API_KEY`       | Kamatera API Key (used together with `KAMATERA_API_SECRET`) to create and manage servers.    |
| `KAMATERA_API_SECRET`    | Kamatera API Secret for authenticating API requests.                                         |
| `KAMATERA_PUBLIC_KEY`    | SSH public key for provisioning servers (injected into cloud instances).                     |
| `KAMATERA_PRIVATE_KEY`   | SSH private key to connect to provisioned servers over SSH.                                  |
| `KAMATERA_ROOT_PASSWORD` | Root password for servers (fallback access if SSH keys fail).                                |

## Terraform Infrastructure Provisioning

This project uses Terraform to create, manage, and destroy infrastructure resources on Kamatera. All servers, networks, and related configurations are fully declarative, enabling reproducible, versioned deployments.

The pipeline for Terraform will extract the `workspace.tfvars` information from the workspace definition and overwrite the file temporaryly in the pipeline. This workspace.tfvars is then used by Terraform to deploy the correct infrastructure.

### Lifecycle

- Generate workspace.tfvars by compiling the workspace definition.
- Generate main.tf by using envsubst to replace variables.
- Initialize Terraform
- Plan Terraform
- Apply Terraform
- Terraform displays server IPs and metadata
- The pipeline cashes the Terraform output

### Important Notes

Private VLAN:

- All servers are connected internally via the VLAN (10.0.0.0/23).
- Always prefer using private IPs for inter-server communication.

Public IPs:

- Every server has a public IP (Kamatera requirement), but only the manager node’s public IP should be used for SSH and public endpoints.

Server Disks:

- You can attach multiple disks by specifying multiple sizes in disks_gb.
- Example: [20, 40] creates a 20GB OS disk + 40GB data disk.

Dynamic Naming:

- Server names are unique and predictable:
- srv-{workspace}-{role}-{index}-{random suffix}

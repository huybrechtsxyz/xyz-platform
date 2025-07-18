# How To Change Platform Infrastructure

This guide shows you how to modify your infrastructure, including adding servers, changing hardware specs, and managing workspaces.

The system only requires you to edit the workspace JSON file. Running the platform pipeline will automatically transform the workspace into Terraform configuration.

## Workflow Overview

You only need to edit the relevant sections of your workspace file and re-run the platform deployment pipeline.

## How to Add a New Server Role or Increase Server Count

- Edit the Workspace File by opening your workspace JSON:

> /deploy/workspaces/{your-environment}.json

Update the roles and server_groups sections. Example to increase worker count:

```json
"server_groups": {
  "worker": {
    "count": 3   // Increase count
  }
}
```

Example to define a new role:

```json
"roles": {
  "extra": {
    "cpu_type": "A",
    "cpu_cores": 2,
    "ram_mb": 4096,
    "unit_cost": 10.00
  }
}
```

## How to Change Hardware Specs

Change any of the role properties in the roles section:

```json
"roles": {
  "worker": {
    "cpu_type": "A",
    "cpu_cores": 2,    // Updated
    "ram_mb": 4096,    // Updated
    "unit_cost": 12.00
  }
}
```

## How to Add Extra Disks

Adjust the disks_gb property in the role definition:

```json
"roles": {
  "infra": {
    "cpu_type": "A",
    "cpu_cores": 1,
    "ram_mb": 4096,
    "disks_gb": [20,40,50]  // Added 50GB disk
  }
}
```

Update any mounts to reference the new disk index:

```json
"mounts": [
  { "type": "logs", "disk": 3 }
]
```

## How to Add a Path Type

Add the new path type in your workspace JSON then update any volume mounts in the workspace definition.

**One mount of type config is required !**

```json
"paths": [
  { "type": "config", "path": "/etc/app"  , "volume"= "replicated"},
  { "type": "cache",  "path": "/var/cache", "volume"= "distributed" }
]
```

## How to Create a New Workspace (Environment)

Copy an existing workspace JSON:

> cp deploy/workspaces/platform.json deploy/workspaces/staging.json

Adjust environment settings and any specific overrides. Commit your changes and run the pipeline for the new environment.

Deploying Changes, after you’ve updated the workspace JSON:

- Commit your changes to the repository.
- Run (or trigger) the platform deployment pipeline.

This pipeline will:

- Transform your workspace JSON into Terraform configuration
- Initialize Terraform
- Plan and apply your infrastructure changes
- Verify the deployment logs and review the applied changes.

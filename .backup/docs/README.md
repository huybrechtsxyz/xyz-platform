# Platform XYZ

## Welcome

Huybrechts-XYZ is a self-hosted platform by Vincent Huybrechts designed to provide centralized authentication, secure service access, and streamlined deployment of custom and third-party applications. The platform enables the user to run various services such as personal websites, blogs, and internally developed software with ease and security.

## Documentation

- [Deploying infrastructure](./infrastructure.md)
- [Troubleshooting guidelines](./troubleshooting.md)

## Getting Started

Follow these steps to get your environment ready for deployment and development.

### 1. Required Accounts

Make sure you have access to the following accounts:

| Account                  | Purpose                                     | Link / Notes                                    |
|--------------------------|---------------------------------------------|-------------------------------------------------|
| **GitHub**               | Access to code repositories                 | https://github.com/                             |
| **Bitwarden**            | Secret management                           | https://bitwarden.com/                          |
| **Terraform Cloud (HCP)**| Infrastructure backend and state management | https://cloud.hashicorp.com/terraform           |
| **Cloud Provider** (*)   | VM provisioning (AWS, Hetzner, etc.)        | Your organization administrator will invite you |

(*) At the moment only Kamatera is supported. This can be expanded by updating the Terraform code.

### 2. Required Tools

Install these tools on your local machine:

| Tool              | Purpose                                   | Install Link / Command                                |
|-------------------|-------------------------------------------|-------------------------------------------------------|
| **Docker**        | Running containers                        | https://www.docker.com/products/docker-desktop        |
| **Terraform CLI** | Provision infrastructure                  | https://developer.hashicorp.com/terraform/downloads   |
| **SSH**           | Connect to servers                        | Pre-installed or via OpenSSH                          |
| **IDE - VSCode**  | Development                               | https://code.visualstudio.com/                        |

### 3. Set Up Accounts

Obtaining Required Tokens for:

#### Bitwarden API Token

You need this token so your CI/CD pipeline can fetch secrets securely.

Steps to get your Bitwarden API token:

- Log in to Bitwarden Web Vault.
- Click your profile icon (top right) → My Account.
- Scroll down to the API Key section.
- Copy the API Key (BW_SESSION).

This is your Bitwarden session token (sometimes referred to as session key or API key).

Set it up as a GitHub Actions secret:

- Go to GitHub → your repository → Settings → Secrets and variables → Actions.
- Click New repository secret.
- Name: BITWARDEN_TOKEN
- Value: (paste your API Key)

**Tip:** Save the Bitwarden secret as secret in Bitwarden for maintenance purposes.

#### Terraform API Token

You need this token to authenticate with Terraform Cloud or HCP.

Steps to get your Terraform Cloud API token:

- Log in to Terraform Cloud.
- Click your user icon (top right) → User Settings.
- In the left menu, select Tokens.
- Under API Tokens, click Create an API token.
- Give it a descriptive name, e.g., platform-deploy.
- Copy the token shown (you can’t retrieve it again).

- Logon to HCP
- Make an organization
- Go to your settings an tokens
- Create a Github App OAuth Token
- go to bitwarden secret amnagement
- create a new secret linked to the machine account

### 4. Set Up Bitwarden Secrets

Secrets are securely managed in Bitwarden Secret Manager.

- Make sure you have access to the **shared vault** (ask a team admin if needed).
- Copy required secrets to your local environment or export them to a file.
- Secrets typically include: `SPECIAL_PASSWORD` (variable name) and a UUID from Bitwarden Secrets.

> **See [Required Workspace Secrets](./infrastructure.md#workspace-secrets).
> **Important:**  
When using the Bitwarden CLI, you *must* replace the example UUIDs with the actual UUIDs for your secrets.  
Each secret has a unique ID that you will find in the Bitwarden web vault under “View Item.”  
Make certain to replace all UUIDs where needed. Variable names are consistent over the platform.

Example command with placeholder UUID:

```bash
bw get item <SECRET-UUID>
```







General Requirements

Ensure your system includes the following:

- Git for repository cloning and source control.
- An IDE like Visual Studio Code.
- A container runtime like Docker desktop or containerd.
- An internet connection :-)

Cloning Sources:

- Clone xyz-platform to get the base infrastructure

Prepare 
- github
- secrets
- create pipline\

Deploying:

- Run the Deploy Platform pipeline to create

## Repository Overview

The repository is organized to cover all aspects of the self-hosted system, including deployment, configuration, and source code.

| Directory                  | Description                                                                 |
|----------------------------|-----------------------------------------------------------------------------|
| `github/workflow/`         | GitHub Actions workflows for CI/CD automation.                             |
| `deploy/scripts/`          | Deployment scripts for installing and updating services.                   |
| `deploy/terraform/`        | Terraform infrastructure-as-code for provisioning servers and cloud resources. |
| `deploy/workspaces/`       | Workspace definition files.                                                |
| `design/`                  | System design documentation and architecture diagrams.                     |
| `docs/`                    | User manuals, technical documentation, and maintenance guides.              |
| `scripts/`                 | Runtime scripts for system maintenance, available for both Windows and Bash environments. |
| `src/`                     | Source code and configuration files, organized by individual service.      |

# Infrastructure Overview

## Infrastructure Provisioning

The infrastructure for this self-hosted platform is created and maintained using **Terraform**, an infrastructure-as-code tool that enables declarative provisioning and management of cloud resources.

- **Supported Provider:**  
  Currently, only **Kamatera** cloud platform is supported for infrastructure deployment.  
  Kamatera supports only servers (CPU, RAM, disks) and network resources.

- **HCP Terraform:**
  Cloud environment for storing the state for Terraform.

- **Workspace Configuration:**  
  Each deployment workspace is described by a JSON configuration file located in the `/deploy` directory.  
  The file is named using the pattern `workspace,{workspaceid}.json`.
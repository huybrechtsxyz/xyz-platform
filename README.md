# XZY - Platform

## Getting Started

### General Requirements

Accounts required

- GitHub Account
- Bitwarden Account
- Bitwarden Secret Manager
- HCP Terraform ( Hashicorp Cloud Platform) account

Development Software required

- Git
- Visual Studio Code
- Docker desktop or related
- Browser

### Setup secret management with bitwarden

- Create a github access token for secret manager (in bitwarden machine - create access token)
- Create in your repository the secret BITWARDEN_TOKEN with value of the access token.

### Setup terraform backend with HCP

- Logon to HCP
- Make an organization
- Go to your settings an tokens
- Create a Github App OAuth Token
- go to bitwarden secret amnagement
- create a new secret linked to the machine account

### Update your secret UUID

In bitwarden secret manager create all the secrets as needed by the workspace file.
Update the secret UUID accordingly.

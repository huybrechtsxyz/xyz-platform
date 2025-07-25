# Makefile for infrastructure setup
include src/develop.env
export

export TF_VAR_api_key := $(KAMATERA_API_KEY)
export TF_VAR_api_secret := $(KAMATERA_API_SECRET)
export TF_VAR_workspace := $(WORKSPACE)
export TF_VAR_password := $(KAMATERA_ROOT_PASSWORD)
export TF_TOKEN_app_terraform_io := $(TF_API_SECRET)

init:
	terraform -chdir=deploy/terraform init

plan:
	terraform -chdir=deploy/terraform plan 

apply:
	terraform -chdir=deploy/terraform apply

destroy:
	terraform -chdir=deploy/terraform destroy

# Convenience target to show all commands
help:
	@echo "Available commands:"
	@echo "  make init      Initialize Terraform"
	@echo "  make plan      Show Terraform plan"
	@echo "  make apply     Apply Terraform changes"
	@echo "  make destroy   Destroy infrastructure"

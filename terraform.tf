# -----
# Defined provider
# -----
provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Course    = "Prod-Pai Cloud Project"
      ManagedBy = "Terraform"
    }
  }
}

# -----
# terraform module
# -----
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket = "prodpai-tfstate-storage"
    key    = "prodpai/terraform.tfstate"
    region = "us-east-1"
  }

}

# -----
# generate key pair for ssh instance
# create file for collect value
# -----
resource "tls_private_key" "custom_key" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "generated_key" {
  key_name   = "${var.project_name}-key"
  public_key = tls_private_key.custom_key.public_key_openssh
}

resource "local_file" "key" {
  filename = pathexpand("~/.ssh/${var.project_name}-key.pem")
  content  = tls_private_key.custom_key.private_key_pem
}

# test hook #2

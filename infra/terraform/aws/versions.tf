terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.60"
    }
  }

  # Estado local de momento; en la fase 13 pasa a S3 con bloqueo nativo (use_lockfile).
}

provider "aws" {
  region  = var.region
  profile = var.profile

  default_tags {
    tags = {
      project     = var.project
      environment = var.environment
      managed_by  = "terraform"
    }
  }
}

terraform {
  required_version = ">= 1.10"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # Estado local de momento; en la fase 14 pasa a un backend azurerm (Storage + bloqueo por lease).
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
  # El proveedor usa Entra ID también para el plano de datos de Storage (sin claves de cuenta).
  storage_use_azuread = true
}

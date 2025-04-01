variable "aws_region" {
  description = "AWS region where resources will be created"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (derived from Git branch name)"
  type        = string
}

variable "config_version" {
  description = "Version of the configuration"
  type        = string
}

variable "config_file_count" {
  description = "Number of configuration files to process"
  type        = number
}

variable "config_file_names" {
  description = "List of configuration file names without extension"
  type        = list(string)
}

variable "config_file_paths" {
  description = "List of paths to the configuration files"
  type        = list(string)
}
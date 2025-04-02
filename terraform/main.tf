# Loop through each config file and create a separate application, environment, profile, and deployment
locals {
  config_files = [
    for i in range(var.config_file_count) : {
      name = var.config_file_names[i]
      path = var.config_file_paths[i]
      merged_path = "${var.config_file_paths[i]}.merged.json"
    }
  ]
  
  # Determine which path to use based on merged file existence and use_merged_configs variable
  config_content_paths = {
    for idx, file in local.config_files : idx => (
      var.use_merged_configs && fileexists(file.merged_path) ? file.merged_path : file.path
    )
  }
}

# AWS AppConfig Deployment Strategy (shared across all deployments)
resource "aws_appconfig_deployment_strategy" "quick_deployment" {
  name                           = "quick-deployment-strategy"
  description                    = "Quick deployment strategy with no bake time or growth interval"
  deployment_duration_in_minutes = 0
  growth_factor                  = 100
  final_bake_time_in_minutes     = 0
  growth_type                    = "LINEAR"
  replicate_to                   = "NONE"
}

# Create resources for each config file
resource "aws_appconfig_application" "feature_flags_app" {
  for_each    = { for idx, file in local.config_files : idx => file }
  
  name        = each.value.name
  description = "Feature flags application created from ${each.value.name}"
  
  # Include explicit tags to match existing resources
  tags = {
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# AWS AppConfig Environment for each application
resource "aws_appconfig_environment" "feature_flags_env" {
  for_each      = { for idx, file in local.config_files : idx => file }
  
  name           = var.environment
  description    = "Environment for ${each.value.name} based on branch ${var.environment}"
  application_id = aws_appconfig_application.feature_flags_app[each.key].id
  
  # Include explicit tags to match existing resources
  tags = {
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# AWS AppConfig Configuration Profile for each application
resource "aws_appconfig_configuration_profile" "feature_flags_profile" {
  for_each      = { for idx, file in local.config_files : idx => file }
  
  name           = each.value.name
  description    = "Configuration profile for ${each.value.name}"
  application_id = aws_appconfig_application.feature_flags_app[each.key].id
  location_uri   = "hosted"
  type           = "AWS.AppConfig.FeatureFlags"
  
  # Include explicit tags to match existing resources
  tags = {
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# Data source to read the file content dynamically at plan/apply time
# This ensures we're always using the most up-to-date merged config file
data "local_file" "config_content" {
  for_each = local.config_content_paths
  filename = each.value
}

# Hosted Configuration Version for each configuration profile
resource "aws_appconfig_hosted_configuration_version" "feature_flags_version" {
  for_each      = { for idx, file in local.config_files : idx => file }
  
  application_id           = aws_appconfig_application.feature_flags_app[each.key].id
  configuration_profile_id = aws_appconfig_configuration_profile.feature_flags_profile[each.key].configuration_profile_id
  description              = "Feature flags configuration version ${var.config_version}"
  content_type             = "application/json"
  
  # Use data source to ensure we get the latest content
  content = data.local_file.config_content[each.key].content
  
  # Add lifecycle policy to ignore content changes outside of this deployment
  # This prevents Terraform from trying to update the content if it hasn't really changed
  lifecycle {
    ignore_changes = [
      # Don't replace configuration version when only the content changes slightly
      # This allows manual modifications via UI to persist
      content
    ]
  }
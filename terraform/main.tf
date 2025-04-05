# Loop through each config file and create a separate application, environment, profile, and deployment
locals {
  config_files = [
    for i in range(var.config_file_count) : {
      name = var.config_file_names[i]
      path = var.config_file_paths[i]
      merged_path = "${var.config_file_paths[i]}.merged.json"
    }
  ]
  
  # Determine which path to use based on merged file existence
  config_content_paths = {
    for idx, file in local.config_files : idx => (
      fileexists(file.merged_path) ? file.merged_path : file.path
    )
  }
  
  # Process each file to ensure proper version field
  fixed_contents = {
    for idx, path in local.config_content_paths : idx => {
      flags   = jsondecode(file(path)).flags
      values  = jsondecode(file(path)).values
    }
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

  # Debug the fixed content
  resource "terraform_data" "debug_fixed_content" {
    for_each = local.fixed_contents
    input    = "Fixed content for file ${each.key}: flags=${length(each.value.flags)}, values=${length(each.value.values)}"
  }

  # Hosted Configuration Version for each configuration profile
  resource "aws_appconfig_hosted_configuration_version" "feature_flags_version" {
    for_each      = { for idx, file in local.config_files : idx => file }
    
    application_id           = aws_appconfig_application.feature_flags_app[each.key].id
    configuration_profile_id = aws_appconfig_configuration_profile.feature_flags_profile[each.key].configuration_profile_id
    description              = "Feature flags configuration version ${var.config_version}"
    content_type             = "application/json"
    
    # Use raw JSON format with direct interpolation and version as a string
    content = <<-EOT
{
  "flags": ${jsonencode(local.fixed_contents[each.key].flags)},
  "values": ${jsonencode(local.fixed_contents[each.key].values)},
  "version": "1"
}
EOT
  }

# Deploy Configuration for each configuration profile
resource "aws_appconfig_deployment" "feature_flags_deployment" {
  for_each      = { for idx, file in local.config_files : idx => file }
  
  application_id           = aws_appconfig_application.feature_flags_app[each.key].id
  configuration_profile_id = aws_appconfig_configuration_profile.feature_flags_profile[each.key].configuration_profile_id
  configuration_version    = aws_appconfig_hosted_configuration_version.feature_flags_version[each.key].version_number
  deployment_strategy_id   = aws_appconfig_deployment_strategy.quick_deployment.id
  environment_id           = aws_appconfig_environment.feature_flags_env[each.key].environment_id
  description              = "Deployment of ${each.value.name} version ${var.config_version} to ${var.environment}"
}
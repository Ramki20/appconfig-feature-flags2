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

# Comprehensive debug for fixed content including attributes and metadata
resource "terraform_data" "debug_fixed_content" {
  for_each = local.fixed_contents
  
  input = {
    file_index = each.key
    counts = {
      flags = length(each.value.flags)
      values = length(each.value.values)
    }
    flags_details = {
      for flag_name, flag_data in each.value.flags : flag_name => {
        name = flag_data.name
        has_attributes = contains(keys(flag_data), "attributes")
        attributes = try(flag_data.attributes, {})
      }
    }
    values_details = {
      for value_name, value_data in each.value.values : value_name => {
        enabled = try(value_data.enabled, null)
        # Dynamically include all other properties
        metadata = {
          for k, v in value_data : k => v if k != "enabled"
        }
      }
    }
  }
}

resource "random_id" "version_id" {
   byte_length = 4
}  

# Hosted Configuration Version for each configuration profile
resource "aws_appconfig_hosted_configuration_version" "feature_flags_version" {
  for_each      = { for idx, file in local.config_files : idx => file }
    
  application_id           = aws_appconfig_application.feature_flags_app[each.key].id
  configuration_profile_id = aws_appconfig_configuration_profile.feature_flags_profile[each.key].configuration_profile_id
  description              = "Feature flags version ${var.config_version} with ${random_id.version_id.hex}"
    
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

# Note: Deployment resource has been removed to allow deployment through Angular UI instead
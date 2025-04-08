output "application_ids" {
  description = "Map of AWS AppConfig Application IDs"
  value       = { for idx, app in aws_appconfig_application.feature_flags_app : app.name => app.id }
}

output "environment_ids" {
  description = "Map of AWS AppConfig Environment IDs"
  value       = { for idx, env in aws_appconfig_environment.feature_flags_env : aws_appconfig_application.feature_flags_app[idx].name => env.id }
}

output "debug_fixed_content" {
  description = "Detailed debug information about fixed content including attributes and metadata"
  value = {
    for k, v in terraform_data.debug_fixed_content : k => jsondecode(jsonencode(v.output))
  }
}

output "configuration_profile_ids" {
  description = "Map of AWS AppConfig Configuration Profile IDs"
  value       = { for idx, profile in aws_appconfig_configuration_profile.feature_flags_profile : aws_appconfig_application.feature_flags_app[idx].name => profile.id }
}

output "deployment_strategy_id" {
  description = "AWS AppConfig Deployment Strategy ID"
  value       = aws_appconfig_deployment_strategy.quick_deployment.id
}

output "hosted_configuration_versions" {
  description = "Map of AWS AppConfig Configuration Version Numbers"
  value       = { for idx, version in aws_appconfig_hosted_configuration_version.feature_flags_version : aws_appconfig_application.feature_flags_app[idx].name => version.version_number }
}

# Removed deployment_ids and deployment_statuses outputs
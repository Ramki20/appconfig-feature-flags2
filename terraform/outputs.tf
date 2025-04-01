output "application_ids" {
  description = "Map of AWS AppConfig Application IDs"
  value       = { for idx, app in aws_appconfig_application.feature_flags_app : app.name => app.id }
}

output "environment_ids" {
  description = "Map of AWS AppConfig Environment IDs"
  value       = { for idx, env in aws_appconfig_environment.feature_flags_env : aws_appconfig_application.feature_flags_app[idx].name => env.id }
}

output "configuration_profile_ids" {
  description = "Map of AWS AppConfig Configuration Profile IDs"
  value       = { for idx, profile in aws_appconfig_configuration_profile.feature_flags_profile : aws_appconfig_application.feature_flags_app[idx].name => profile.id }
}

output "deployment_strategy_id" {
  description = "AWS AppConfig Deployment Strategy ID"
  value       = aws_appconfig_deployment_strategy.quick_deployment.id
}

output "deployment_ids" {
  description = "Map of AWS AppConfig Deployment IDs"
  value       = { for idx, deployment in aws_appconfig_deployment.feature_flags_deployment : aws_appconfig_application.feature_flags_app[idx].name => deployment.id }
}

output "deployment_statuses" {
  description = "Map of AWS AppConfig Deployment Statuses"
  value       = { for idx, deployment in aws_appconfig_deployment.feature_flags_deployment : aws_appconfig_application.feature_flags_app[idx].name => deployment.state }
}
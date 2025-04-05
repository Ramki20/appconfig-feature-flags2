#!/usr/bin/env python3
import json
import boto3
import argparse
import os
import logging
import sys
from botocore.exceptions import ClientError
import base64

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('appconfig-merger')

def parse_arguments():
    parser = argparse.ArgumentParser(description='Merge AWS AppConfig feature flags with existing configuration')
    parser.add_argument('--config-file', required=True, help='Path to the feature flags JSON file')
    parser.add_argument('--app-name', required=True, help='AWS AppConfig application name')
    parser.add_argument('--env-name', required=True, help='AWS AppConfig environment name')
    parser.add_argument('--profile-name', required=True, help='AWS AppConfig profile name')
    parser.add_argument('--force-create', action='store_true', help='Force create new configuration if none exists')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--output-file', help='Path to write the merged configuration (defaults to same as input with .merged.json suffix)')
    
    return parser.parse_args()

def load_terraform_config(file_path):
    """Load the Terraform-defined configuration from a JSON file"""
    try:
        with open(file_path, 'r') as f:
            config = json.load(f)
        
        # Validate the basic structure 
        if not all(key in config for key in ["flags", "values"]):
            logger.error(f"Config file {file_path} is missing required keys 'flags' and/or 'values'")
            sys.exit(1)
            
        return config
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON file {file_path}: {str(e)}")
        sys.exit(1)
    except FileNotFoundError:
        logger.error(f"Config file {file_path} not found")
        sys.exit(1)

def get_latest_configuration_version(client, app_id, profile_id):
    """Get the latest configuration version from the profile, regardless of deployment status"""
    try:
        # List all versions for this profile
        response = client.list_hosted_configuration_versions(
            ApplicationId=app_id,
            ConfigurationProfileId=profile_id
        )
        
        # If there are no versions, return None
        if not response.get('Items'):
            logger.warning(f"No configuration versions found for profile ID: {profile_id}")
            return None, None
        
        # The versions are returned in descending order with the newest first
        latest_version = response['Items'][0]
        version_number = latest_version['VersionNumber']
        
        # Now get the content of this version
        content_response = client.get_hosted_configuration_version(
            ApplicationId=app_id,
            ConfigurationProfileId=profile_id,
            VersionNumber=version_number
        )
        
        # Decode the content from bytes to string, then parse JSON
        content_bytes = content_response['Content']
        content_string = content_bytes.read().decode('utf-8')
        
        try:
            configuration = json.loads(content_string)
            logger.info(f"Retrieved latest configuration version: {version_number}")
            
            return configuration, version_number
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing configuration content: {str(e)}")
            return None, None
            
    except ClientError as e:
        logger.error(f"Error retrieving latest configuration version: {str(e)}")
        return None, None

def get_current_appconfig(client, application_name, environment_name, profile_name):
    """Get the current configuration from AWS AppConfig's configuration profile"""
    try:
        # First, get the application ID
        app_response = client.list_applications()
        app_id = None
        
        for app in app_response['Items']:
            if app['Name'] == application_name:
                app_id = app['Id']
                logger.info(f"Found application '{application_name}' with ID: {app_id}")
                break
        
        if not app_id:
            logger.warning(f"Application '{application_name}' not found in AWS AppConfig")
            return None, None
        
        # Next, get the environment ID
        env_response = client.list_environments(ApplicationId=app_id)
        env_id = None
        
        for env in env_response['Items']:
            if env['Name'] == environment_name:
                env_id = env['Id']
                logger.info(f"Found environment '{environment_name}' with ID: {env_id}")
                break
        
        if not env_id:
            logger.warning(f"Environment '{environment_name}' not found in AWS AppConfig")
            return None, None
        
        # Then, get the configuration profile ID
        profile_response = client.list_configuration_profiles(ApplicationId=app_id)
        profile_id = None
        
        for profile in profile_response['Items']:
            if profile['Name'] == profile_name:
                profile_id = profile['Id']
                logger.info(f"Found configuration profile '{profile_name}' with ID: {profile_id}")
                break
        
        if not profile_id:
            logger.warning(f"Configuration profile '{profile_name}' not found in AWS AppConfig")
            return None, None
        
        # Instead of getting the deployed configuration, get the latest configuration version
        return get_latest_configuration_version(client, app_id, profile_id)
            
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            logger.warning("No existing configuration found")
            return None, None
        else:
            logger.error(f"Error retrieving current configuration: {str(e)}")
            return None, None

def create_merged_config(terraform_config, current_config, current_version):
    """Create a merged configuration that preserves existing values and metadata fields"""
    # If no current configuration exists, just use the terraform config
    if not current_config:
        logger.info("No existing configuration found, using terraform configuration as-is")
        return terraform_config
    
    # Create a new merged configuration
    # Start with the structure from Terraform
    merged_config = {
        "flags": terraform_config["flags"],
        "values": {},
        "version": "1"  # AWS AppConfig Feature Flags requires version as a string
    }
    
    # Track changes for logging
    added_flags = set(terraform_config["flags"].keys()) - set(current_config.get("flags", {}).keys())
    removed_flags = set(current_config.get("flags", {}).keys()) - set(terraform_config["flags"].keys())
    modified_flags = []
    preserved_flags = []
    
    # For each flag in the Terraform structure (these are the flags we want to keep)
    for flag_name in terraform_config["flags"].keys():
        flag_def = terraform_config["flags"].get(flag_name, {})
        
        if flag_name in current_config.get("values", {}):
            # Start with existing values from AWS AppConfig for flags that already exist
            logger.info(f"Preserving existing values for flag: {flag_name}")
            merged_config["values"][flag_name] = current_config["values"][flag_name].copy()
            preserved_flags.append(flag_name)
            
            # Preserve metadata fields for existing flags
            # These fields typically start with an underscore (_)
            for key in current_config["values"][flag_name]:
                if key.startswith('_'):
                    logger.info(f"Preserving metadata field {key} for flag: {flag_name}")
                    merged_config["values"][flag_name][key] = current_config["values"][flag_name][key]
            
            # Check for any new attributes that might be in terraform but not in current config
            if "attributes" in flag_def:
                for attr_name in flag_def.get("attributes", {}):
                    # If this is a new attribute not present in current values, add it with default
                    if attr_name not in merged_config["values"][flag_name] and attr_name in terraform_config["values"].get(flag_name, {}):
                        logger.info(f"Adding new attribute '{attr_name}' to existing flag: {flag_name}")
                        merged_config["values"][flag_name][attr_name] = terraform_config["values"][flag_name][attr_name]
                        
                        # Track this flag as modified
                        if flag_name not in [m["flag"] for m in modified_flags]:
                            modified_flags.append({
                                "flag": flag_name,
                                "added_attrs": [attr_name],
                                "removed_attrs": []
                            })
        else:
            # Use default values from Terraform JSON for new flags
            logger.info(f"Adding new flag with default values: {flag_name}")
            merged_config["values"][flag_name] = terraform_config["values"].get(flag_name, {"enabled": "false"}).copy()
            
            # For new flags, we need to simulate metadata fields like _createdAt
            # AWS AppConfig will add these automatically upon deployment
            # We're not adding them here to avoid inconsistencies with AWS AppConfig's own behavior
    
    # Check if any metadata fields exist at the top level of current_config and preserve them
    for key in current_config:
        if key.startswith('_') and key not in merged_config:
            logger.info(f"Preserving top-level metadata field: {key}")
            merged_config[key] = current_config[key]
    
    # Display detailed log of changes
    if added_flags:
        logger.info(f"Adding flags: {added_flags}")
    
    if removed_flags:
        logger.info(f"Removing flags: {removed_flags}")
    
    if preserved_flags:
        logger.info(f"Preserving existing values for flags: {preserved_flags}")
    
    if modified_flags:
        logger.info(f"Modifying flag attributes: {json.dumps(modified_flags)}")
    
    # Also track attribute changes that aren't additions
    attr_modified_flags = []
    for flag_name in set(terraform_config["flags"].keys()) & set(current_config.get("flags", {}).keys()):
        tf_attrs = set(terraform_config["flags"].get(flag_name, {}).get("attributes", {}).keys())
        current_attrs = set(current_config.get("flags", {}).get(flag_name, {}).get("attributes", {}).keys())
        
        # Check for attributes that are in the current config but not in Terraform (these will be removed)
        if current_attrs - tf_attrs:
            attr_modified_flags.append({
                "flag": flag_name,
                "added_attrs": [],
                "removed_attrs": list(current_attrs - tf_attrs)
            })
            logger.warning(f"Flag '{flag_name}' has attributes that will be removed: {list(current_attrs - tf_attrs)}")
    
    if attr_modified_flags:
        logger.info(f"Flags with attributes being removed: {json.dumps(attr_modified_flags)}")
    
    logger.info(f"Configuration version updated from {current_version} to \"1\" (AWS requires version as a string value)")
    
    # Perform a final validation check
    if len(merged_config["flags"]) != len(merged_config["values"]):
        logger.error(f"Configuration mismatch: {len(merged_config['flags'])} flags defined but {len(merged_config['values'])} value sets")
        logger.error(f"Flags defined: {list(merged_config['flags'].keys())}")
        logger.error(f"Values defined: {list(merged_config['values'].keys())}")
        
        # Find the differences
        missing_values = set(merged_config["flags"].keys()) - set(merged_config["values"].keys())
        extra_values = set(merged_config["values"].keys()) - set(merged_config["flags"].keys())
        
        if missing_values:
            logger.error(f"Flags missing values: {missing_values}")
        if extra_values:
            logger.error(f"Values without flag definitions: {extra_values}")
        
        sys.exit(1)
    
    return merged_config

def main():
    args = parse_arguments()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    logger.info(f"Processing configuration file: {args.config_file}")
    logger.info(f"Using AppConfig application: {args.app_name}")
    logger.info(f"Using AppConfig environment: {args.env_name}")
    logger.info(f"Using AppConfig profile: {args.profile_name}")
    
    # Load the terraform-defined configuration
    terraform_config = load_terraform_config(args.config_file)
    
    # Initialize the AWS AppConfig client
    client = boto3.client('appconfig')
    
    # Get the current configuration from AWS AppConfig's configuration profile
    current_config, current_version = get_current_appconfig(client, args.app_name, args.env_name, args.profile_name)
    
    if not current_config and not args.force_create:
        logger.error("No existing configuration found and --force-create not specified")
        logger.error("Exiting without making changes")
        sys.exit(1)
    
    # If no current config and force create is set, use terraform config as-is
    if not current_config and args.force_create:
        logger.info("Creating new configuration from terraform file")
        merged_config = terraform_config
    else:
        # Create the merged configuration
        merged_config = create_merged_config(terraform_config, current_config, current_version or "0")
    
    # Determine the output file path
    if args.output_file:
        output_path = args.output_file
    else:
        output_path = f"{args.config_file}.merged.json"
    
    # Write the merged configuration to the output file
    with open(output_path, 'w') as f:
        json.dump(merged_config, f, indent=2)
    
    # Log the merged JSON contents for debugging
    logger.info(f"Merged configuration written to: {output_path}")
    logger.info("Merged JSON contents:")
    logger.info(json.dumps(merged_config, indent=2))
    
    # Exit with success code
    sys.exit(0)

if __name__ == "__main__":
    main()
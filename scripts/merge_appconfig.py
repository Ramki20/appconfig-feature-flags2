#!/usr/bin/env python3
import json
import boto3
import argparse
import os
import logging
import sys
from botocore.exceptions import ClientError

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
    """Load the GitHub-defined configuration from a JSON file"""
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
        
        # Get the latest configuration version
        return get_latest_configuration_version(client, app_id, profile_id)
            
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            logger.warning("No existing configuration found")
            return None, None
        else:
            logger.error(f"Error retrieving current configuration: {str(e)}")
            return None, None

def create_merged_config(github_config, aws_config, current_version):
    """Create a merged configuration that preserves all AWS AppConfig values and metadata"""
    # If no AWS configuration exists, just use the GitHub config as-is
    if not aws_config:
        logger.info("No existing configuration found in AWS, using GitHub configuration as-is")
        return github_config
    
    # Start with a new configuration object with the flags defined in GitHub
    merged_config = {
        "flags": github_config["flags"].copy(),
        "values": {},
        "version": "1"  # AWS AppConfig Feature Flags requires version as a string
    }
    logger.info(f"merged_config-1: {merged_config}")
    
    # Track changes for logging
    added_flags = set(github_config["flags"].keys()) - set(aws_config.get("flags", {}).keys())
    removed_flags = set(aws_config.get("flags", {}).keys()) - set(github_config["flags"].keys())
    preserved_flags = []
    
    # For each flag in GitHub (these are the flags we want to keep)
    for flag_name in github_config["flags"].keys():
        if flag_name in aws_config.get("values", {}):
            # If flag exists in AWS AppConfig, preserve ALL its values and metadata
            logger.info(f"Preserving existing values and metadata for flag: {flag_name}")
            merged_config["values"][flag_name] = aws_config["values"][flag_name].copy()
            preserved_flags.append(flag_name)
        else:
            # For new flags not in AWS AppConfig, use default values from GitHub
            logger.info(f"Adding new flag with default values: {flag_name}")
            merged_config["values"][flag_name] = github_config["values"].get(flag_name, {"enabled": "false"}).copy()


    logger.info(f"merged_config-2: {merged_config}")
    
    # Copy any top-level metadata fields from AWS AppConfig
    for key in aws_config:
        if key.startswith('_') and key not in merged_config:
            logger.info(f"Preserving top-level metadata field: {key}")
            merged_config[key] = aws_config[key]
    
    # Display detailed log of changes
    if added_flags:
        logger.info(f"Adding flags: {added_flags}")
    
    if removed_flags:
        logger.info(f"Removing flags: {removed_flags}")
    
    if preserved_flags:
        logger.info(f"Preserving existing values for flags: {preserved_flags}")
    
    # Update the version field
    logger.info(f"Configuration version updated from {current_version} to \"1\" (AWS requires version as a string value)")
    
    logger.info(f"merged_config-3: {merged_config}")
    
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

def check_if_file_changed(output_path, merged_config):
    """Check if the output file exists and is different from the merged config"""
    if not os.path.exists(output_path):
        logger.info(f"Output file {output_path} doesn't exist yet")
        return True
        
    try:
        with open(output_path, 'r') as f:
            existing_content = json.load(f)
            
        # Compare only the structure (flags and their attributes)
        # without comparing values or metadata
        existing_flags = set(existing_content.get("flags", {}).keys())
        merged_flags = set(merged_config.get("flags", {}).keys())
        
        if existing_flags != merged_flags:
            logger.info(f"Flag sets are different: existing={existing_flags}, merged={merged_flags}")
            return True
            
        # More detailed check for flag attributes
        for flag_name in merged_flags:
            if flag_name not in existing_content.get("flags", {}):
                logger.info(f"Flag {flag_name} exists in merged but not in existing")
                return True
                
            merged_attrs = merged_config["flags"][flag_name].get("attributes", {})
            existing_attrs = existing_content["flags"][flag_name].get("attributes", {})
            
            if set(merged_attrs.keys()) != set(existing_attrs.keys()):
                logger.info(f"Attributes for flag {flag_name} are different")
                return True
        
        logger.info("No structural changes detected in configuration")
        return False
    except Exception as e:
        logger.warning(f"Error checking existing file: {str(e)}")
        return True

def write_output_file(content, output_path):
    """Write merged configuration to output file"""
    try:
        # Create directory if it doesn't exist
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        with open(output_path, 'w') as f:
            json.dump(content, f, indent=2)
            
        logger.info(f"Successfully wrote merged configuration to: {output_path}")
        return True
    except Exception as e:
        logger.error(f"Error writing output file: {str(e)}")
        return False

def main():
    args = parse_arguments()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    logger.info(f"Processing configuration file: {args.config_file}")
    logger.info(f"Using AppConfig application: {args.app_name}")
    logger.info(f"Using AppConfig environment: {args.env_name}")
    logger.info(f"Using AppConfig profile: {args.profile_name}")
    
    # Load the GitHub-defined configuration
    github_config = load_terraform_config(args.config_file)
    
    # Initialize the AWS AppConfig client
    client = boto3.client('appconfig')
    
    # Get the current configuration from AWS AppConfig
    aws_config, current_version = get_current_appconfig(client, args.app_name, args.env_name, args.profile_name)
    
    if not aws_config and not args.force_create:
        logger.error("No existing configuration found in AWS AppConfig and --force-create not specified")
        logger.error("Exiting without making changes")
        sys.exit(1)
    
    # Create the merged configuration
    merged_config = create_merged_config(github_config, aws_config, current_version or "0")
    
    # Determine the output file path
    if args.output_file:
        output_path = args.output_file
    else:
        output_path = f"{args.config_file}.merged.json"
    
    # Check if the file has actually changed
    if check_if_file_changed(output_path, merged_config):
        logger.info("Writing updated merged configuration")
        if not write_output_file(merged_config, output_path):
            sys.exit(1)
    else:
        logger.info(f"No structural changes detected. Keeping existing file: {output_path}")
    
    # Output the merged configuration to logs for debugging
    logger.info("Merged configuration content:")
    with open(output_path, 'r') as f:
        logger.info(f.read())
    
    # Exit with success
    sys.exit(0)

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
import json
import boto3
import argparse
import os
import logging
import sys
from botocore.exceptions import ClientError
import base64
import hashlib
import shutil
import datetime

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
    parser.add_argument('--force-write', action='store_true', help='Force write even if no functional changes')
    
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

def normalize_for_comparison(obj):
    """Normalize object for comparison by removing metadata fields"""
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            # Skip metadata fields starting with underscore for comparison
            if not key.startswith('_'):
                result[key] = normalize_for_comparison(value)
        return result
    elif isinstance(obj, list):
        # Sort lists to ensure consistent ordering
        if all(isinstance(item, (int, float, str, bool)) for item in obj):
            # For simple types, just sort the list
            return sorted(obj)
        else:
            # For complex types, normalize each item and return (can't sort complex objects)
            return [normalize_for_comparison(item) for item in obj]
    else:
        return obj

def configs_are_functionally_equivalent(config1, config2):
    """Compare two configurations, ignoring metadata fields"""
    # Create normalized versions for comparison
    normalized1 = {
        "flags": normalize_for_comparison(config1.get("flags", {})),
        "values": normalize_for_comparison(config1.get("values", {})),
        "version": normalize_for_comparison(config1.get("version", ""))
    }
    
    normalized2 = {
        "flags": normalize_for_comparison(config2.get("flags", {})),
        "values": normalize_for_comparison(config2.get("values", {})),
        "version": normalize_for_comparison(config2.get("version", ""))
    }
    
    # Convert to JSON strings for comparison (ensures consistent ordering)
    json_str1 = json.dumps(normalized1, sort_keys=True)
    json_str2 = json.dumps(normalized2, sort_keys=True)
    
    # Debug output to help identify differences
    if json_str1 != json_str2:
        logger.info("Functional differences detected in configurations:")
        # First, check if the flag definitions are different
        if json.dumps(normalized1["flags"], sort_keys=True) != json.dumps(normalized2["flags"], sort_keys=True):
            logger.info("  - Flag definitions are different")
        
        # Then check if the values (ignoring metadata) are different
        if json.dumps(normalized1["values"], sort_keys=True) != json.dumps(normalized2["values"], sort_keys=True):
            logger.info("  - Flag values are different")
            
            # Find specific flags that differ
            flags1 = set(normalized1["values"].keys())
            flags2 = set(normalized2["values"].keys())
            
            if flags1 != flags2:
                added = flags1 - flags2
                removed = flags2 - flags1
                if added:
                    logger.info(f"    - Added flags: {added}")
                if removed:
                    logger.info(f"    - Removed flags: {removed}")
                    
            # For flags in both configs, check if values differ
            common_flags = flags1.intersection(flags2)
            for flag in common_flags:
                val1 = normalized1["values"].get(flag, {})
                val2 = normalized2["values"].get(flag, {})
                if json.dumps(val1, sort_keys=True) != json.dumps(val2, sort_keys=True):
                    logger.info(f"    - Flag '{flag}' has different values")
                    
        # Check if version is different
        if normalized1["version"] != normalized2["version"]:
            logger.info(f"  - Version differs: {normalized1['version']} vs {normalized2['version']}")
    
    # Calculate hash for efficient comparison
    hash1 = hashlib.md5(json_str1.encode()).hexdigest()
    hash2 = hashlib.md5(json_str2.encode()).hexdigest()
    
    are_equivalent = hash1 == hash2
    
    if are_equivalent:
        logger.info("Configurations are functionally equivalent (ignoring metadata)")
    else:
        logger.info("Configurations are functionally different")
        
    # Debug output to help diagnose issues
    logger.info(f"Hash of normalized config1: {hash1}")
    logger.info(f"Hash of normalized config2: {hash2}")
        
    return are_equivalent

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

def check_existing_merged_file(output_path, merged_config):
    """Check if the existing merged file is functionally equivalent to the new one"""
    if os.path.exists(output_path):
        try:
            with open(output_path, 'r') as f:
                existing_config = json.load(f)
                
            logger.info(f"Comparing with existing merged file: {output_path}")
            
            # Compare the configs, ignoring metadata fields
            return configs_are_functionally_equivalent(existing_config, merged_config)
        except Exception as e:
            logger.warning(f"Error comparing with existing file: {str(e)}")
            return False
    else:
        logger.info(f"No existing merged file found at: {output_path}")
        return False

def safely_write_file(content, output_path):
    """Write content to file in a way that works across filesystems"""
    # Create a temporary file in the same directory as the output file
    # This avoids cross-device link errors
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
        except Exception as e:
            logger.error(f"Failed to create directory {output_dir}: {str(e)}")
            sys.exit(1)
    
    # Use a temporary file in the same directory
    temp_path = f"{output_path}.tmp"
    try:
        with open(temp_path, 'w') as f:
            json.dump(content, f, indent=2)
        
        # Replace the target file with the temporary one
        # First remove the target file if it exists
        if os.path.exists(output_path):
            os.remove(output_path)
        
        # Then rename the temporary file
        os.rename(temp_path, output_path)
        return True
    except Exception as e:
        logger.error(f"Error writing file: {str(e)}")
        # Clean up the temporary file if it exists
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False

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
    
    # Check if the merged configuration is functionally equivalent to the existing one
    no_changes = check_existing_merged_file(output_path, merged_config)
    
    if no_changes and not args.force_write:
        logger.info(f"No functional changes detected. Reusing existing merged file: {output_path}")
    else:
        if no_changes:
            logger.info(f"No functional changes detected, but forcing write due to --force-write flag")
        
        # Write the merged configuration safely to the output file
        if safely_write_file(merged_config, output_path):
            logger.info(f"Merged configuration written to: {output_path}")
        else:
            sys.exit(1)
    
    # Log the merged JSON contents for debugging
    logger.info("Merged JSON contents:")
    try:
        with open(output_path, 'r') as f:
            config_content = f.read()
            logger.info(config_content)
    except Exception as e:
        logger.error(f"Error reading merged configuration: {str(e)}")
    
    # Exit with success code
    sys.exit(0)

if __name__ == "__main__":
    main()
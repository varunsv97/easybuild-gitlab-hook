#!/usr/bin/env python3
"""
Script to inject default configuration into EasyBuild-generated GitLab CI pipelines.

This script reads configuration from .gitlab-ci.yml and applies it to the generated pipeline.

Usage:
    python inject_defaults.py <pipeline_file> [config_file]
    
Example:
    python inject_defaults.py easybuild-child-pipeline.yml .gitlab-ci.yml
"""

import sys
import yaml
from pathlib import Path


def load_config_from_gitlab_ci(config_file):
    """Load default configuration from .gitlab-ci.yml file."""
    
    if not config_file.exists():
        print(f"Warning: Config file '{config_file}' not found, using minimal defaults")
        return {}, {}
    
    try:
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        
        # Extract the 'default' section from .gitlab-ci.yml
        default_config = config_data.get('default', {})
        
        # Extract variables from execute_builds job for child pipeline
        child_variables = {}
        execute_builds = config_data.get('execute_builds', {})
        if 'variables' in execute_builds:
            child_variables = execute_builds['variables']
        
        return default_config, child_variables
    except Exception as e:
        print(f"Warning: Could not load config file: {e}")
        return {}, {}


def inject_pipeline_metadata(data, config, child_variables):
    """Inject configuration from .gitlab-ci.yml into the pipeline."""
    
    # Get or create default section
    default = data.get('default', {})
    
    # Copy before_script from config
    if 'before_script' in config:
        default['before_script'] = config['before_script'].copy()
    
    # Copy after_script from config if present
    if 'after_script' in config:
        default['after_script'] = config['after_script'].copy()
    
    # Copy tags from config
    if 'tags' in config:
        default['tags'] = config['tags'].copy()
    
    # Copy id_tokens from config
    if 'id_tokens' in config:
        default['id_tokens'] = config['id_tokens'].copy()
    
    # Copy retry configuration if present
    if 'retry' in config:
        default['retry'] = config['retry'].copy()
    else:
        # Add default retry configuration
        default['retry'] = {
            'max': 2,
            'when': ['runner_system_failure', 'stuck_or_timeout_failure', 'job_execution_timeout']
        }
    
    # Copy timeout if present
    if 'timeout' in config:
        default['timeout'] = config['timeout']
    
    # Copy image if present
    if 'image' in config:
        default['image'] = config['image']
    
    # Update the data with our modified default section
    data['default'] = default
    
    # Merge child pipeline variables into the global variables section
    if child_variables:
        variables = data.get('variables', {})
        variables.update(child_variables)
        data['variables'] = variables


def main():
    """Main function to process the pipeline file."""
    
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python inject_defaults.py <pipeline_file> [config_file]")
        print("Example: python inject_defaults.py easybuild-child-pipeline.yml .gitlab-ci.yml")
        sys.exit(1)
    
    pipeline_file = Path(sys.argv[1])
    config_file = Path(sys.argv[2]) if len(sys.argv) == 3 else Path('.gitlab-ci.yml')
    
    if not pipeline_file.exists():
        print(f"Error: Pipeline file '{pipeline_file}' not found!")
        sys.exit(1)
    
    print(f"Processing pipeline file: {pipeline_file}")
    print(f"Reading config from: {config_file}")
    
    # Load configuration from .gitlab-ci.yml
    config, child_variables = load_config_from_gitlab_ci(config_file)
    
    # Load the pipeline YAML
    try:
        with open(pipeline_file, 'r') as f:
            pipeline_data = yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading pipeline file: {e}")
        sys.exit(1)
    
    if not pipeline_data:
        print("Error: Pipeline file is empty or invalid!")
        sys.exit(1)
    
    # Inject the configuration
    inject_pipeline_metadata(pipeline_data, config, child_variables)
    
    # Write the updated pipeline back
    try:
        with open(pipeline_file, 'w') as f:
            yaml.dump(pipeline_data, f, default_flow_style=False, width=120, sort_keys=False)
    except Exception as e:
        print(f"Error writing pipeline file: {e}")
        sys.exit(1)
    
    print("✅ Successfully injected configuration from .gitlab-ci.yml!")
    
    # Show summary of what was added
    default_config = pipeline_data.get('default', {})
    variables_config = pipeline_data.get('variables', {})
    
    print(f"\nApplied configuration:")
    if 'image' in default_config:
        print(f"  - Image: {default_config.get('image')}")
    if 'tags' in default_config:
        print(f"  - Tags: {', '.join(default_config.get('tags', []))}")
    if 'retry' in default_config:
        print(f"  - Retry max: {default_config.get('retry', {}).get('max')}")
    if 'timeout' in default_config:
        print(f"  - Timeout: {default_config.get('timeout')}")
    if 'before_script' in default_config:
        print(f"  - Before script steps: {len(default_config.get('before_script', []))}")
    if 'after_script' in default_config:
        print(f"  - After script steps: {len(default_config.get('after_script', []))}")
    if 'id_tokens' in default_config:
        print(f"  - JWT tokens configured: Yes")
    if variables_config:
        print(f"  - Variables: {', '.join(variables_config.keys())}")


if __name__ == '__main__':
    main()

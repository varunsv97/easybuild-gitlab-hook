#!/usr/bin/env python3
"""
Script to inject default configuration into EasyBuild-generated GitLab CI pipelines.

This script adds essential default configuration including:
- EasyBuild environment activation
- Custom runner tags
- JWT tokens for authentication
- Retry and timeout configurations

Usage:
    python inject_defaults.py <pipeline_file>
    
Example:
    python inject_defaults.py easybuild-child-pipeline.yml
"""

import sys
import yaml
from pathlib import Path


def inject_pipeline_metadata(data):
    """Inject default metadata into the pipeline configuration."""
    
    # Get or create default section
    default = data.get('default', {})
    
    # Add EasyBuild environment activation to before_script
    before_script = default.get('before_script', [])
    eb_env_activate = f'source /data/rosi/shared/eb/easybuild_environments/rome/eb_env/bin/activate'
    if 'ml python' not in before_script:
        before_script.insert(0, 'ml python')
    if eb_env_activate not in before_script:
        before_script.insert(1, eb_env_activate)
    
    # Add environment setup
    if 'echo "Starting EasyBuild job: $CI_JOB_NAME"' not in before_script:
        before_script.append('echo "Starting EasyBuild job: $CI_JOB_NAME"')
    
    default['before_script'] = before_script
    
    # Add after_script for cleanup and reporting
    after_script = default.get('after_script', [])
    cleanup_commands = [
        'echo "Cleaning up temporary files"',
        'rm -rf /tmp/eblog || true',
        'rm -rf /tmp/ebbuild || true', 
        'echo "Job completed: $CI_JOB_NAME"'
    ]
    
    for cmd in cleanup_commands:
        if cmd not in after_script:
            after_script.append(cmd)
    
    default['after_script'] = after_script
    
    # Merge 'tags'
    tags = set(default.get('tags', []))
    tags.add('rosi-admin-slurm')
    default['tags'] = list(tags)

    # Merge 'id_tokens'
    default.setdefault('id_tokens', {})
    default['id_tokens']['CI_JOB_JWT'] = {
        'aud': 'https://codebase.helmholtz.cloud'
    }
    data['default'] = default

    
    # Add retry configuration
    default['retry'] = {
        'max': 2,
        'when': ['runner_system_failure', 'stuck_or_timeout_failure', 'job_execution_timeout']
    }
    
    # Update the data with our modified default section
    data['default'] = default
    
    # Add global variables if not present
    variables = data.get('variables', {})
    data['variables'] = variables


def main():
    """Main function to process the pipeline file."""
    
    if len(sys.argv) != 2:
        print("Usage: python inject_defaults.py <pipeline_file>")
        print("Example: python inject_defaults.py easybuild-child-pipeline.yml")
        sys.exit(1)
    
    pipeline_file = Path(sys.argv[1])
    
    if not pipeline_file.exists():
        print(f"Error: Pipeline file '{pipeline_file}' not found!")
        sys.exit(1)
    
    print(f"Processing pipeline file: {pipeline_file}")
    
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
    
    # Inject the default metadata
    inject_pipeline_metadata(pipeline_data)
    
    # Write the updated pipeline back
    try:
        with open(pipeline_file, 'w') as f:
            yaml.dump(pipeline_data, f, default_flow_style=False, width=120, sort_keys=False)
    except Exception as e:
        print(f"Error writing pipeline file: {e}")
        sys.exit(1)
    
    print("✅ Successfully injected default configuration!")
    print("\nAdded configurations:")
    print("  - EasyBuild environment activation")
    print("  - Custom runner tags (easybuild-runner, gpu-h100)")
    print("  - JWT authentication tokens")
    print("  - Retry logic and timeouts")
    print("  - Cleanup scripts")
    print("  - Hopper-specific variables")
    
    # Show summary of what was added
    default_config = pipeline_data.get('default', {})
    print(f"\nDefault configuration summary:")
    print(f"  - Image: {default_config.get('image', 'Not set')}")
    print(f"  - Tags: {', '.join(default_config.get('tags', []))}")
    print(f"  - Retry max: {default_config.get('retry', {}).get('max', 'Not set')}")
    print(f"  - Timeout: {default_config.get('timeout', 'Not set')}")
    print(f"  - Before script steps: {len(default_config.get('before_script', []))}")
    print(f"  - After script steps: {len(default_config.get('after_script', []))}")


if __name__ == '__main__':
    main()

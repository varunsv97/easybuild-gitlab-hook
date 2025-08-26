site_configuration = {
    'systems': [
        {
            'name': 'rosi',
            'descr': 'Slurm-based cluster',
            'hostnames': ['.*'],
            'modules_system': 'lmod',
            'partitions': [
                {
                    'name': 'rome',
                    'descr': 'CPU Rome',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access' : ['--partition=cpu-rome'],
                    'modules': ['rome'],
                    'environs': ['easybuild'],
                    'container_platforms': [
                        {'type': 'Apptainer'}
                        ],
                    'resources': [
                        {
                            'name': 'memory',
                            'options': ['--mem={memory}']
                        }
                    ]
                },
                {
                    'name': 'genoa',
                    'descr': 'CPU Genoa',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access' : ['--partition=cpu-genoa'],
                    'modules': ['genoa'],
                    'environs': ['easybuild'],
                    'container_platforms': [
                        {'type': 'Apptainer'}
                        ],
                    'resources': [
                        {
                            'name': 'memory',
                            'options': ['--mem={memory}']
                        }
                    ]
                },
                {
                    'name': 'milan',
                    'descr': 'CPU Milan',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access': ['--partition=cpu-milan'],
                    'modules': ['milan'],
                    'environs': ['easybuild'],
                    'container_platforms': [{'type': 'Apptainer'}],
                    'resources': [
                        {'name': 'memory', 'options': ['--mem={memory}']}
                    ]
                },
                {
                    'name': 'turin',
                    'descr': 'CPU Turin',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access': ['--partition=cpu-turin'],
                    'modules': ['turin'],
                    'environs': ['easybuild'],
                    'container_platforms': [{'type': 'Apptainer'}],
                    'resources': [
                        {'name': 'memory', 'options': ['--mem={memory}']}
                    ]
                },
                {
                    'name': 'ampere',
                    'descr': 'GPU ampere (A100)',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access' : ['--partition=gpu-a100'],
                    'modules': ['ampere'],
                    'environs': ['easybuild'],
                    'container_platforms': [
                        {'type': 'Apptainer'}
                        ],
                    'resources': [ {
                        'name': 'gpu',
                        'options': ['--gres=gpu:{num_gpus_per_node}']
                    },
                    {
                        'name': 'memory',
                        'options': ['--mem={memory}']
                        }
                                 ]

                },
                {
                    'name': 'hopper',
                    'descr': 'GPU Hopper (H100)',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access': ['--partition=gpu-h100'],
                    'modules': ['hopper'],
                    'environs': ['easybuild'],
                    'container_platforms': [{'type': 'Apptainer'}],
                    'resources': [
                        {'name': 'gpu', 'options': ['--gres=gpu:{num_gpus_per_node}']},
                        {'name': 'memory', 'options': ['--mem={memory}']}
                    ]
                },
                {
                    'name': 'blackwell',
                    'descr': 'GPU Blackwell (B200)',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access': ['--partition=gpu-b200'],
                    'modules': ['blackwell'],
                    'environs': ['easybuild'],
                    'container_platforms': [{'type': 'Apptainer'}],
                    'resources': [
                        {'name': 'gpu', 'options': ['--gres=gpu:{num_gpus_per_node}']},
                        {'name': 'memory', 'options': ['--mem={memory}']}
                    ]
                },
            ]
        }
    ],
    'environments': [
        {
            'name': 'baseline',
        },
        {
            'name': 'easybuild',
            'features': ['easybuild'],
            'prepare_cmds': [
                'module load python',
                'source /data/rosi/shared/eb/easybuild_environments/rome/eb_env/bin/activate'
            ]
        },
        
    ],

    'general' : [
        {
            'save_log_files' : True,
            'keep_stage_files' : True,
        }
    ]
}
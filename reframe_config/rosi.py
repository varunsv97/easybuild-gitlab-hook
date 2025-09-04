site_configuration = {
    'systems': [
        {
            'name': 'rosi',
            'descr': 'Slurm-based cluster',
            'stagedir': '/bigdata/rz/rosi_easybuild/reframe_logs/stage',
            'outputdir': '/bigdata/rz/rosi_easybuild/reframe_logs/output',
            'hostnames': ['.*'],
            'modules_system': 'lmod',
            'partitions': [
                {
                    'name': 'rome',
                    'descr': 'Compute Rome',
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
                    'descr': 'Compute Genoa',
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
                    'descr': 'Compute Milan',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access' : ['--partition=cpu-milan'],
                    'modules': ['milan'],
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
                    'name': 'ampere',
                    'descr': 'GPU ampere',
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
                    'descr': 'GPU hopper',
                    'scheduler': 'slurm',
                    'launcher': 'local',
                    'access' : ['--partition=gpu-h100'],
                    'modules': ['hopper'],
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

                }
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
        },
        
    ],
    'modes': [
        {
            'name': 'ci-generate',
        }
    ],

    'general' : [
        {
            'save_log_files' : True,
            'keep_stage_files' : True,
        }
    ]
}
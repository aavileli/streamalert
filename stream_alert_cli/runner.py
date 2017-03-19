'''
Copyright 2017-present, Airbnb Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''

import logging
import os

from collections import namedtuple
from jinja2 import Environment, PackageLoader

from stream_alert_cli.package import RuleProcessorPackage, AlertProcessorPackage
from stream_alert_cli.version import LambdaVersion
from stream_alert_cli.test import stream_alert_test
from stream_alert_cli.helpers import CLIHelpers
from stream_alert_cli.config import CLIConfig

from stream_alert.alert_processor import __version__ as alert_processor_version
from stream_alert.rule_processor import __version__ as rule_processor_version


class InvalidClusterName(Exception):
    """Exception for invalid cluster names"""
    pass


CONFIG = CLIConfig()


def cli_runner(options):
    """Main Stream Alert CLI handler

    Args:
        options (dict): command line arguments passed from the argparser.
            Contains the following keys for terraform commands:
                (command, subcommand, target)
            Contains the following keys for lambda commands:
                (command, subcommand, env, func, source)
    """
    cli_load_message = ('Stream Alert CLI'
                        '\nIssues? Report here: '
                        'https://github.com/airbnb/streamalert/issues')
    logging.info(cli_load_message)

    if options.command == 'lambda':
        lambda_runner(options)

    elif options.command == 'terraform':
        terraform_runner(options)


def lambda_runner(options):
    """Handle all Lambda CLI operations"""
    if options.subcommand == 'deploy':
        deploy(options)

    elif options.subcommand == 'rollback':
        rollback()
        targets = ['module.stream_alert_{}'.format(x)
                   for x in CONFIG['clusters'].keys()]
        tf_runner(targets=targets)

    elif options.subcommand == 'test':
        stream_alert_test(options)


def terraform_check():
    """Verify that Terraform is configured correctly"""
    prereqs_message = ('Terraform not found! Please install and add to'
                       'your $PATH:\n'
                       '$ export PATH=$PATH:/usr/local/terraform/bin')
    run_command(['terraform', 'version'],
                error_message=prereqs_message,
                quiet=True)


def terraform_runner(options):
    """Handle all Terraform CLI operations"""
    # verify terraform is installed
    terraform_check()
    # use a named tuple to match the 'processor' attribute in the argparse options
    deploy_opts = namedtuple('DeployOptions', ['processor'])

    # plan/apply our streamalert infrastructure
    if options.subcommand == 'build':
        # --target is for terraforming a specific streamalert module
        if options.target:
            target = options.target
            targets = ['module.{}_{}'.format(target, cluster)
                       for cluster in CONFIG['clusters'].keys()]
            tf_runner(targets=targets)
        else:
            tf_runner()

    # generate terraform files
    elif options.subcommand == 'generate':
        generate_tf_files()

    # initialize streamalert infrastructure from a blank state
    elif options.subcommand == 'init':
        logging.info('Initializing StreamAlert')
        logging.info('Generating Cluster Files')
        generate_tf_files()

        # build init infrastructure
        logging.info('Building Initial Infrastructure')
        init_targets = [
            'aws_s3_bucket.lambda_source',
            'aws_s3_bucket.integration_testing',
            'aws_s3_bucket.terraform_remote_state',
            'aws_kms_key.stream_alert_secrets',
            'aws_kms_alias.stream_alert_secrets'
        ]
        tf_runner(targets=init_targets, refresh_state=False)

        logging.info('Deploying Lambda Functions')
        # setup remote state
        refresh_tf_state()
        # deploy both lambda functions
        deploy(deploy_opts('all'))
        # create all remainder infrastructure
        logging.info('Building Remainder Infrastructure')
        tf_runner()
        # refresh config to get modified variables
        # refresh_config()

    # destroy all infrastructure
    elif options.subcommand == 'destroy':
        run_command(['terraform', 'remote', 'config', '-disable'])
        tf_runner(action='destroy', refresh_state=False)

    # get a quick status on our declared infrastructure
    elif options.subcommand == 'status':
        status()


def run_command(args=None, **kwargs):
    """Alias to CLI Helpers.run_command"""
    return CLIHelpers.run_command(args, **kwargs)


def continue_prompt():
    """Continue prompt used before applying Terraform plans"""
    required_responses = {'yes', 'no'}
    response = ''
    while response not in required_responses:
        response = raw_input('\nWould you like to continue? (yes or no): ')
    if response == 'yes':
        return True
    return False


def refresh_tf_state():
    """Refresh the Terraform remote state"""
    logging.info('Refreshing Remote State config')
    region = CONFIG['account']['region']
    bucket = '{}.streamalert.terraform.state'.format(CONFIG['account']['prefix'])
    s3_key = CONFIG['terraform']['tfstate_s3_key']
    kms_key_id = 'alias/{}'.format(CONFIG['account']['kms_key_alias'])

    remote_state_opts = [
        'terraform',
        'remote',
        'config',
        '-backend=s3',
        '-backend-config=bucket={}'.format(bucket),
        '-backend-config=key={}'.format(s3_key),
        '-backend-config=region={}'.format(region),
        '-backend-config=kms_key_id={}'.format(kms_key_id),
        '-backend-config=encrypt=true'
    ]

    run_command(remote_state_opts, quiet=True)


def tf_runner(**kwargs):
    """Terraform wrapper to build StreamAlert infrastructure.

    Steps:
        - resolve modules with `terraform get`
        - run `terraform plan` for the given targets
        - if plan is successful and user confirms prompt,
          then the infrastructure is applied.

    kwargs:
        targets: a list of Terraform targets
        action: 'apply' or 'destroy'
        refresh_state: boolean to refresh remote state or not

    Returns: Boolean result of if the terraform command
             was successful or not
    """
    targets = kwargs.get('targets', [])
    action = kwargs.get('action', None)
    refresh_state = kwargs.get('refresh_state', True)
    tf_action_index = 1  # The index to the terraform 'action'

    tf_opts = ['-var-file=../{}'.format(CONFIG.filename)]
    tf_targets = ['-target={}'.format(x) for x in targets]
    tf_command = ['terraform', 'plan'] + tf_opts + tf_targets
    if action == 'destroy':
        tf_command.append('-destroy')

    if refresh_state:
        refresh_tf_state()

    logging.info('Resolving Terraform modules')
    run_command(['terraform', 'get'], quiet=True)

    logging.info('Planning infrastructure')
    tf_plan = run_command(tf_command) and continue_prompt()
    if not tf_plan:
        return False

    if action == 'destroy':
        logging.info('Destroying infrastructure')
        tf_command[tf_action_index] = action
        tf_command.remove('-destroy')

    elif action:
        tf_command[tf_action_index] = action

    else:
        logging.info('Creating infrastructure')
        tf_command[tf_action_index] = 'apply'

    run_command(tf_command)
    return True


def status():
    """Display current AWS infrastructure built by Terraform"""
    print 'Cluster Info\n'
    for cluster, region in CONFIG['clusters'].iteritems():
        print '==== {} ==='.format(cluster)
        print 'Region: {}'.format(region)
        print ('Lambda settings: \n\tTimeout: {}\n\tMemory: {}'
               '\n\tProd Version: {}').format(
                   CONFIG['lambda_settings'][cluster][0],
                   CONFIG['lambda_settings'][cluster][1],
                   CONFIG['lambda_function_prod_versions'][cluster])
        print 'Kinesis settings: \n\tShards: {}\n\tRetention: {}'.format(
            CONFIG['kinesis_settings'][cluster][0],
            CONFIG['kinesis_settings'][cluster][1]
        )
        print '\n'

    print 'User access keys'
    run_command(['terraform', 'output'])


def rollback():
    """Rollback the current production AWS Lambda version by 1

    Notes:
        Ignores if the production version is $LATEST
        Only rollsback if published version is greater than 1
    """
    clusters = CONFIG['clusters'].keys()
    for cluster in clusters:
        for lambda_function in ('rule_processor', 'alert_processor'):
            current_vers = CONFIG['{}_versions'][cluster]
            if current_vers != '$LATEST':
                current_vers = int(current_vers)
                if current_vers > 1:
                    new_vers = current_vers - 1
                    CONFIG['{}_versions'][cluster] = new_vers


def generate_tf_files():
    """Generate all Terraform plans for the clusters in variables.json"""
    env = Environment(loader=PackageLoader('terraform', 'templates'))
    template = env.get_template('cluster_template')

    all_buckets = CONFIG.get('s3_event_buckets')

    for cluster in CONFIG['clusters'].keys():
        if cluster == 'main':
            raise InvalidClusterName('Rename cluster main to something else!')

        if all_buckets:
            buckets = all_buckets.get(cluster)
        else:
            buckets = None

        contents = template.render(cluster_name=cluster, s3_buckets=buckets)
        with open('terraform/{}.tf'.format(cluster), 'w') as tf_file:
            tf_file.write(contents)


def deploy(options):
    """Deploy new versions of both Lambda functions

    Steps:
    - build lambda deployment package
    - upload to S3
    - update variables.json with uploaded package hash/key
    - publish latest version
    - update variables.json with latest published version
    - terraform apply
    """
    processor = options.processor
    # terraform apply only to the module which contains our lambda functions
    targets = ['module.stream_alert_{}'.format(x)
               for x in CONFIG['clusters'].keys()]

    def deploy_rule_processor():
        """Create Rule Processor package and publish versions"""
        package = RuleProcessorPackage(
            config=CONFIG,
            version=rule_processor_version
        ).create_and_upload()

    def deploy_alert_processor():
        """Create Alert Processor package and publish versions"""
        package = AlertProcessorPackage(
            config=CONFIG,
            version=alert_processor_version
        ).create_and_upload()

    if processor == 'rule':
        deploy_rule_processor()

    elif processor == 'alert':
        deploy_alert_processor()

    elif processor == 'all':
        deploy_rule_processor()
        deploy_alert_processor()

    tf_runner(targets=targets)

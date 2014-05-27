#!/usr/bin/env python2.7
#
# Copyright 2014 Canonical Ltd. released under AGPL
#
# Authors:
#  Tycho Andersen <tycho.andersen@canonical.com>
#

import glob
import os
import sys
import shutil
import subprocess

from charmhelpers.fetch import apt_install
from charmhelpers.core.hookenv import (
    Hooks,
    UnregisteredHookError,
    config,
    log
)

from charmhelpers.contrib.openstack.context import IdentityServiceContext
from charmhelpers.contrib.openstack.utils import get_os_codename_package
from charmhelpers.contrib.openstack.templating import OSConfigRenderer

CONF_FILE_DIR = os.environ.get('SIMPLESTREAMS_GLANCE_SYNC_CONF_DIR',
                               '/etc/simplestreams-glance-sync')
MIRRORS_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

release = get_os_codename_package('glance-common', fatal=False) or 'icehouse'
configs = OSConfigRenderer(templates_dir='templates/',
                           openstack_release=release)

configs.register(MIRRORS_CONF_FILE_NAME, [config])
configs.register(ID_CONF_FILE_NAME, [IdentityServiceContext()])

SCRIPT_NAME = "glance-simplestreams-sync.py"

hooks = Hooks()


def install_cron_script():
    """Installs sync program to /etc/cron.daily/

    Script is not a template but we always overwrite, to ensure it is
    up-to-date.

    """
    source = "scripts/" + SCRIPT_NAME
    destdir = '/etc/cron.{frequency}'.format(frequency=config('frequency'))
    shutil.copy(source, destdir)


def uninstall_cron_script():
    """removes sync program from any place it might be"""
    for fn in glob.glob("/etc/cron.*/"+ SCRIPT_NAME):
        if os.path.exists(fn):
            os.remove(fn)


def run_sync():
    """Run the sync script.
    Note that it will fail to run if all the config files are not in place.
    We allow that, since future hook executions will also call run_sync().
    """
    log.debug("Running sync script directly")
    try:
        output = subprocess.check_output(os.path.join("scripts", SCRIPT_NAME),
                                         stderr=subprocess.STDOUT)
        log.debug("Output from sync script run: {}".format(output))
    except subprocess.CalledProcessError as e:
        log.exception("Nonzero exit from single sync: {}".format(e.returnCode))


@hooks.hook('identity-service-relation-changed')
def identity_service_changed():
    """
    TODOs:
    - handle other ID service hook events
    """
    configs.write(ID_CONF_FILE_NAME)
    run_sync()


@hooks.hook('install')
def install():
    apt_install(packages=['python-simplestreams', 'python-glanceclient',
                          'python-yaml',
                          'python-swiftclient', 'ubuntu-cloudimage-keyring'])

    log('end install hook.')


@hooks.hook('config-changed')
def config_changed():
    log('begin config-changed hook.')

    configs.write(MIRRORS_CONF_FILE_NAME)

    if config.changed('run'):
        if not config['run']:
            log('"run" config disabled, uninstalling cronjob and exiting')
            uninstall_cron_script()
        else:
            install_cron_script()
            run_sync()


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))

#!/usr/bin/env python2.7
#
# Copyright 2014 Canonical Ltd. released under AGPL
#
# Authors:
#  Tycho Andersen <tycho.andersen@canonical.com>
#

# This file is part of the glance-simplestreams sync charm.
#
# The glance-simplestreams sync charm is free software: you can
# redistribute it and/or modify it under the terms of the GNU Affero General
# Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version.
#
# The charm is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this charm.  If not, see <http://www.gnu.org/licenses/>.

import glob
import os
import sys
import shutil
import subprocess

from charmhelpers.fetch import apt_install
from charmhelpers.core import hookenv

from charmhelpers.contrib.openstack.context import (IdentityServiceContext,
                                                    OSContextGenerator)
from charmhelpers.contrib.openstack.utils import get_os_codename_package
from charmhelpers.contrib.openstack.templating import OSConfigRenderer

CONF_FILE_DIR = os.environ.get('SIMPLESTREAMS_GLANCE_SYNC_CONF_DIR',
                               '/etc/simplestreams-glance-sync')

MIRRORS_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

SCRIPT_NAME = "glance-simplestreams-sync.py"

hooks = hookenv.Hooks()


class MirrorsConfigServiceContext(OSContextGenerator):
    """Context for mirrors.yaml template - does not use relation info.
    """
    interfaces = ['simplestreams-image-service']

    def __call__(self):
        hookenv.log("Generating template context for simplestreams-image-service")
        config = hookenv.config()
        return dict(mirror_list=config['mirror_list'])


release = get_os_codename_package('glance-common', fatal=False) or 'icehouse'
configs = OSConfigRenderer(templates_dir='templates/',
                           openstack_release=release)

configs.register(MIRRORS_CONF_FILE_NAME, [MirrorsConfigServiceContext()])
configs.register(ID_CONF_FILE_NAME, [IdentityServiceContext()])


def install_cron_script():
    """Installs sync program to /etc/cron.daily/

    Script is not a template but we always overwrite, to ensure it is
    up-to-date.

    """
    source = "scripts/" + SCRIPT_NAME
    config = hookenv.config()
    destdir = '/etc/cron.{frequency}'.format(frequency=config['frequency'])
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
    hookenv.log("Running sync script directly")
    try:
        output = subprocess.check_output(os.path.join("scripts", SCRIPT_NAME),
                                         stderr=subprocess.STDOUT)
        hookenv.log("Output from sync script run: {}".format(output))
    except subprocess.CalledProcessError as e:
        hookenv.log("Nonzero exit from single sync: {}".format(e.returnCode))


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
    hookenv.log("creating config dir at {}".format(CONF_FILE_DIR))
    if not os.path.isdir(CONF_FILE_DIR):
        if os.path.exists(CONF_FILE_DIR):
            hookenv.log("error: CONF_FILE_DIR exists but is not a directory. exiting.")
            return
        os.mkdir(CONF_FILE_DIR)

    apt_install(packages=['python-simplestreams', 'python-glanceclient',
                          'python-yaml',
                          'python-swiftclient', 'ubuntu-cloudimage-keyring'])

    hookenv.log('end install hook.')


@hooks.hook('config-changed')
def config_changed():
    hookenv.log('begin config-changed hook.')

    configs.write(MIRRORS_CONF_FILE_NAME)

    config = hookenv.config()
    if config.changed('run'):
        hookenv.log("removing existing cron jobs for simplestreams sync")
        uninstall_cron_script()

        if not config['run']:
            hookenv.log("'run' config disabled, exiting")
        else:
            hookenv.log("'run' config enabled, installing to "
                "/etc/cron.{}".format(config['frequency']))
            install_cron_script()
            hookenv.log("Running initial sync")
            run_sync()


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except hookenv.UnregisteredHookError as e:
        hookenv.log('Unknown hook {} - skipping.'.format(e))

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

from charmhelpers.fetch import apt_install
from charmhelpers.core import hookenv

from charmhelpers.contrib.openstack.context import (IdentityServiceContext,
                                                    OSContextGenerator)
from charmhelpers.contrib.openstack.utils import get_os_codename_package
from charmhelpers.contrib.openstack.templating import OSConfigRenderer

CONF_FILE_DIR = '/etc/glance-simplestreams-sync'

MIRRORS_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

SCRIPT_NAME = "glance-simplestreams-sync.py"

CRON_POLL_FILENAME = 'glance_simplestreams_sync_fastpoll'
CRON_POLL_FILEPATH = os.path.join('/etc/cron.d', CRON_POLL_FILENAME)

hooks = hookenv.Hooks()


class MirrorsConfigServiceContext(OSContextGenerator):
    """Context for mirrors.yaml template - does not use relation info.
    """
    interfaces = ['simplestreams-image-service']

    def __call__(self):
        hookenv.log("Generating template ctxt for simplestreams-image-service")
        config = hookenv.config()
        return dict(mirror_list=config['mirror_list'])


release = get_os_codename_package('glance-common', fatal=False) or 'icehouse'
configs = OSConfigRenderer(templates_dir='templates/',
                           openstack_release=release)

configs.register(MIRRORS_CONF_FILE_NAME, [MirrorsConfigServiceContext()])
configs.register(ID_CONF_FILE_NAME, [IdentityServiceContext()])


def install_cron_scripts():
    """Installs two cron jobs.
    one in /etc/cron.$frequency/ to sync script for repeating sync

    one in /cron.d every-minute job in crontab for quick polling.

    Script is not a template but we always overwrite, to ensure it is
    up-to-date.

    """
    sync_script_source = "scripts/" + SCRIPT_NAME
    shutil.copy(sync_script_source, CONF_FILE_DIR)

    config = hookenv.config()
    installed_script = os.path.join(CONF_FILE_DIR, SCRIPT_NAME)
    linkname = '/etc/cron.{f}/{s}'.format(frequency=config['frequency'],
                                          s=SCRIPT_NAME)
    os.symlink(installed_script, linkname)

    poll_file_source = os.path.join('scripts', CRON_POLL_FILENAME)
    shutil.copy(poll_file_source, '/etc/cron.d/')


def uninstall_cron_scripts():
    """Removes sync program from any place it might be, and removes
    polling cron job."""
    for fn in glob.glob("/etc/cron.*/" + SCRIPT_NAME):
        if os.path.exists(fn):
            os.remove(fn)

    if os.path.exists(CRON_POLL_FILEPATH):
        os.remove(CRON_POLL_FILEPATH)


@hooks.hook('identity-service-relation-joined')
def identity_service_joined(relation_id=None):
    # generate bogus service url to make keystone happy.
    # we will not be starting anything to pay attention to this URL.
    url = 'http://' + hookenv.unit_get('private-address')
    relation_data = {
        'service': 'image-stream',
        'region': 'RegionOne',  # config('region'),
        'public_url': url,
        'admin_url': url,
        'internal_url': url}

    hookenv.relation_set(relation_id=relation_id, **relation_data)


@hooks.hook('identity-service-relation-changed')
def identity_service_changed():
    configs.write(ID_CONF_FILE_NAME)


@hooks.hook('install')
def install():
    hookenv.log("creating config dir at {}".format(CONF_FILE_DIR))
    if not os.path.isdir(CONF_FILE_DIR):
        if os.path.exists(CONF_FILE_DIR):
            hookenv.log("error: CONF_FILE_DIR exists"
                        " but is not a directory. exiting.")
            return
        os.mkdir(CONF_FILE_DIR)

    apt_install(packages=['python-simplestreams', 'python-glanceclient',
                          'python-yaml', 'python-keystoneclient',
                          'python-swiftclient', 'ubuntu-cloudimage-keyring'])

    install_cron_scripts()

    hookenv.log('end install hook.')


@hooks.hook('config-changed')
def config_changed():
    hookenv.log('begin config-changed hook.')

    configs.write(MIRRORS_CONF_FILE_NAME)

    config = hookenv.config()
    if config.changed('run'):
        hookenv.log("removing existing cron jobs for simplestreams sync")
        uninstall_cron_scripts()

        if not config['run']:
            hookenv.log("'run' config disabled, exiting")
        else:
            hookenv.log("'run' config enabled, installing to "
                        "/etc/cron.{}".format(config['frequency']))
            hookenv.log("installing {} for polling".format(CRON_POLL_FILEPATH))
            install_cron_scripts()


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except hookenv.UnregisteredHookError as e:
        hookenv.log('Unknown hook {} - skipping.'.format(e))

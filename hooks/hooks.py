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

from charmhelpers.fetch import apt_install, add_source, apt_update
from charmhelpers.core import hookenv
from charmhelpers.payload.execd import execd_preinstall

from charmhelpers.contrib.openstack.context import (AMQPContext,
                                                    IdentityServiceContext,
                                                    OSContextGenerator)
from charmhelpers.contrib.openstack.utils import get_os_codename_package
from charmhelpers.contrib.openstack.templating import OSConfigRenderer

CONF_FILE_DIR = '/etc/glance-simplestreams-sync'
USR_SHARE_DIR = '/usr/share/glance-simplestreams-sync'

MIRRORS_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

SCRIPT_NAME = "glance-simplestreams-sync.py"

CRON_JOB_FILENAME = 'glance_simplestreams_sync'
CRON_POLL_FILENAME = 'glance_simplestreams_sync_fastpoll'
CRON_POLL_FILEPATH = os.path.join('/etc/cron.d', CRON_POLL_FILENAME)

hooks = hookenv.Hooks()


class MultipleImageModifierSubordinatesIsNotSupported(Exception):
    """Raise this if multiple image-modifier subordinates are related to
    this charm.
    """


class MirrorsConfigServiceContext(OSContextGenerator):
    """Context for mirrors.yaml template.

    Uses image-modifier relation if available to set
    modify_hook_scripts config value.

    """
    interfaces = ['simplestreams-image-service']

    def __call__(self):
        hookenv.log("Generating template ctxt for simplestreams-image-service")
        config = hookenv.config()

        modify_hook_scripts = []
        image_modifiers = hookenv.relations_of_type('image-modifier')
        if len(image_modifiers) > 1:
            raise MultipleImageModifierSubordinatesIsNotSupported()

        if len(image_modifiers) == 1:
            im = image_modifiers[0]
            try:
                modify_hook_scripts.append(im['script-path'])

            except KeyError as ke:
                hookenv.log('relation {} yielded '
                            'exception {} - ignoring.'.format(repr(im),
                                                              repr(ke)))

        # default no-op so that None still means "missing" for config
        # validation (see elsewhere)
        if len(modify_hook_scripts) == 0:
            modify_hook_scripts.append('/bin/true')

        return dict(mirror_list=config['mirror_list'],
                    modify_hook_scripts=', '.join(modify_hook_scripts),
                    name_prefix=config['name_prefix'],
                    content_id_template=config['content_id_template'],
                    use_swift=config['use_swift'],
                    region=config['region'],
                    cloud_name=config['cloud_name'])


class JujuProxyContext(OSContextGenerator):
    """Context for http(s)-proxy and no-proxy juju environment settings"""

    def __call__(self):
        d = {}
        for v in ['http_proxy', 'https_proxy', 'no_proxy']:
            if v in os.environ:
                d[v] = os.environ[v]
        return d


release = get_os_codename_package('glance-common', fatal=False) or 'icehouse'
configs = OSConfigRenderer(templates_dir='templates/',
                           openstack_release=release)

configs.register(MIRRORS_CONF_FILE_NAME, [MirrorsConfigServiceContext()])
configs.register(ID_CONF_FILE_NAME, [IdentityServiceContext(),
                                     AMQPContext(),
                                     JujuProxyContext()])


def install_cron_script():
    """Installs cron job in /etc/cron.$frequency/ for repeating sync

    Script is not a template but we always overwrite, to ensure it is
    up-to-date.

    """
    sync_script_source = os.path.join("scripts", SCRIPT_NAME)
    shutil.copy(sync_script_source, USR_SHARE_DIR)

    config = hookenv.config()
    installed_script = os.path.join(USR_SHARE_DIR, SCRIPT_NAME)
    linkname = '/etc/cron.{f}/{s}'.format(f=config['frequency'],
                                          s=CRON_JOB_FILENAME)
    os.symlink(installed_script, linkname)


def install_cron_poll():
    "Installs /etc/cron.d every-minute job in crontab for quick polling."
    poll_file_source = os.path.join('scripts', CRON_POLL_FILENAME)
    shutil.copy(poll_file_source, '/etc/cron.d/')


def uninstall_cron_script():
    "Removes sync program from any cron place it might be"
    for fn in glob.glob("/etc/cron.*/" + CRON_JOB_FILENAME):
        if os.path.exists(fn):
            os.remove(fn)


def uninstall_cron_poll():
    "Removes cron poll"
    if os.path.exists(CRON_POLL_FILEPATH):
        os.remove(CRON_POLL_FILEPATH)


@hooks.hook('identity-service-relation-joined')
def identity_service_joined(relation_id=None):
    config = hookenv.config()

    # Generate temporary bogus service URL to make keystone charm
    # happy. The sync script will replace it with the endpoint for
    # swift, because when this hook is fired, we do not yet
    # necessarily know the swift endpoint URL (it might not even exist
    # yet).

    url = 'http://' + hookenv.unit_get('private-address')
    relation_data = {
        'service': 'image-stream',
        'region': config['region'],
        'public_url': url,
        'admin_url': url,
        'internal_url': url}

    hookenv.relation_set(relation_id=relation_id, **relation_data)


@hooks.hook('identity-service-relation-changed')
def identity_service_changed():
    configs.write(ID_CONF_FILE_NAME)


@hooks.hook('install')
def install():
    execd_preinstall()
    for directory in [CONF_FILE_DIR, USR_SHARE_DIR]:
        hookenv.log("creating config dir at {}".format(directory))
        if not os.path.isdir(directory):
            if os.path.exists(directory):
                hookenv.log("error: {} exists but is not a directory."
                            " exiting.".format(directory))
                return
            os.mkdir(directory)

    hookenv.log('adding cloud-installer PPA')
    add_source('ppa:cloud-installer/simplestreams-testing')
    apt_update()

    apt_install(packages=['python-simplestreams', 'python-glanceclient',
                          'python-yaml', 'python-keystoneclient',
                          'python-kombu',
                          'python-swiftclient', 'ubuntu-cloudimage-keyring'])

    hookenv.log('end install hook.')


@hooks.hook('config-changed',
            'image-modifier-relation-changed',
            'image-modifier-relation-joined')
def config_changed():
    hookenv.log('begin config-changed hook.')

    configs.write(MIRRORS_CONF_FILE_NAME)
    configs.write(ID_CONF_FILE_NAME)

    config = hookenv.config()

    if config.changed('frequency'):
        hookenv.log("'frequency' changed, removing cron job")
        uninstall_cron_script()
        if config['run']:
            hookenv.log("moving cron job to "
                        "/etc/cron.{}".format(config['frequency']))
            install_cron_script()

    if config.changed('run'):
        hookenv.log("'run' changed, removing existing cron jobs")
        uninstall_cron_script()
        uninstall_cron_poll()

        if not config['run']:
            hookenv.log("'run' config now disabled, exiting")
        else:
            hookenv.log("'run' config now enabled, installing to "
                        "/etc/cron.{}".format(config['frequency']))
            hookenv.log("installing {} for polling".format(CRON_POLL_FILEPATH))
            install_cron_poll()
            install_cron_script()
    config.save()


@hooks.hook('upgrade-charm')
def upgrade_charm():
    install()
    configs.write_all()


@hooks.hook('amqp-relation-joined')
def amqp_joined():
    conf = hookenv.config()
    hookenv.relation_set(username=conf['rabbit-user'],
                         vhost=conf['rabbit-vhost'])


@hooks.hook('amqp-relation-changed')
def amqp_changed():
    if 'amqp' not in configs.complete_contexts():
        hookenv.log('amqp relation incomplete. Peer not ready?')
        return
    configs.write(ID_CONF_FILE_NAME)


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except hookenv.UnregisteredHookError as e:
        hookenv.log('Unknown hook {} - skipping.'.format(e))

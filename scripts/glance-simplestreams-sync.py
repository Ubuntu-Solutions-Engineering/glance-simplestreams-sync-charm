#!/usr/bin/env python2.7
#
# Copyright 2014 Canonical Ltd.
#
# This file is part of the glance-simplestreams sync charm.

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

# This script runs as a cron job installed by the
# glance-simplestreams-sync juju charm.  It reads config files that
# are written by the hooks of that charm based on its config and
# juju relation to keystone. However, it does not execute in a
# juju hook context itself.

import logging
import os
from simplestreams.mirrors import glance, UrlMirrorReader
from simplestreams.objectstores.swift import SwiftObjectStore
from simplestreams.util import read_signed, path_from_mirror_url
import sys
import yaml

KEYRING = '/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg'
CONF_FILE_DIR = os.environ.get('GLANCE_SIMPLESTREAMS_SYNC_CONF_DIR',
                               '/etc/glance-simplestreams-sync')
MIRRORS_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

# juju looks in simplestreams/data/* in swift to figure out which
# images to deploy, so this path isn't really configurable even though
# it is.
SWIFT_DATA_DIR = 'simplestreams/data/'

# TODOs:
#   - We might want to allow people to set regions as well, so
#     you can have one charm sync to one region, instead of doing a cross
#     region sync.
#   - allow people to specify their own policy, since they can specify
#     their own mirrors.
#   - potentially allow people to specify backup mirrors?
#   - debug keyring support
#   - figure out what content_id is and whether we should allow users to
#     set it


def setup_logging():
    logfilename = '/var/log/glance-simplestreams-sync.log'
    h = logging.FileHandler(logfilename)
    h.setFormatter(logging.Formatter(
        '%(levelname)-9s * %(asctime)s [PID:%(process)d] * %(name)s * '
        '%(message)s',
        datefmt='%m-%d %H:%M:%S'))

    logger = logging.getLogger('')
    logger.setLevel('DEBUG')
    logger.addHandler(h)

    return logger


log = setup_logging()


def policy(content, path):
    if path.endswith('sjson'):
        return read_signed(content, keyring=KEYRING)
    else:
        return content


def read_conf(filename):
    with open(filename) as f:
        confobj = yaml.load(f)
    return confobj


def get_conf():
    conf_files = [ID_CONF_FILE_NAME, MIRRORS_CONF_FILE_NAME]
    for conf_file_name in conf_files:
        if not os.path.exists(conf_file_name):
            log.info("{} does not exist, exiting.".format(conf_file_name))
            sys.exit(1)

    id_conf = read_conf(ID_CONF_FILE_NAME)
    if None in id_conf.values():
        log.info("Configuration value missing in {}:\n"
                 "{}".format(ID_CONF_FILE_NAME, id_conf))
        sys.exit(1)
    mirrors = read_conf(MIRRORS_CONF_FILE_NAME)
    if None in mirrors.values():
        log.info("Configuration value missing in {}:\n"
                 "{}".format(MIRRORS_CONF_FILE_NAME, mirrors))
        sys.exit(1)

    return id_conf, mirrors


def set_openstack_env(id_conf):
    auth_url = '%s://%s:%s/v2.0' % (id_conf['auth_protocol'],
                                    id_conf['auth_host'],
                                    id_conf['auth_port'])
    os.environ['OS_AUTH_URL'] = auth_url
    os.environ['OS_USERNAME'] = id_conf['admin_user']
    os.environ['OS_PASSWORD'] = id_conf['admin_password']
    os.environ['OS_TENANT_ID'] = id_conf['admin_tenant_id']


def do_sync(mirrors):

    for mirror_info in mirrors['mirror_list']:
        mirror_url, initial_path = path_from_mirror_url(mirror_info['url'],
                                                        mirror_info['path'])

        log.info("configuring sync for url {}".format(mirror_info))

        smirror = UrlMirrorReader(mirror_url, policy=policy)

        store = SwiftObjectStore(SWIFT_DATA_DIR)

        config = {'max_items': mirror_info['max'],
                  'keep': False,
                  'content_id': 'auto.sync'}

        tmirror = glance.GlanceMirror(config=config, objectstore=store)
        log.info("calling GlanceMirror.sync")
        tmirror.sync(smirror, path=initial_path)


if __name__ == "__main__":

    id_conf, mirrors = get_conf()

    set_openstack_env(id_conf)

    do_sync(mirrors)

#!/usr/bin/env python2.7
#
# Copyright 2014 Canonical Ltd. released under AGPL
#
# Authors:
#  Tycho Andersen <tycho.andersen@canonical.com>
#

import os
import sys
import subprocess
import yaml

from textwrap import dedent

from charmhelpers.fetch import apt_install
from charmhelpers.core.hookenv import (
    ERROR,
    Hooks,
    UnregisteredHookError,
    config,
    log,
    relation_set,
    unit_get,
)


from charmhelpers.contrib.openstack.context import IdentityServiceContext


hooks = Hooks()


def glance_sync_program():
    # Here things in {}s are variables, and dictionaries. obviously broken, but
    # it's a start.

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

    return dedent("""
        import os
        from simplestreams.mirrors import glance, UrlMirrorReader
        from simplestreams.objectstores.swift import SwiftObjectStore
        from simplestreams.util import read_signed, path_from_mirror_url

        auth_url = '%s://%s:%s/v2.0' % ({auth_protocol}, {auth_host}, {auth_port})
        os.environ['OS_AUTH_URL'] = auth_url
        os.environ['OS_USERNAME'] = {admin_user}
        os.environ['OS_PASSWORD'] = {admin_password}
        os.environ['OS_TENANT_ID'] = {admin_tenant_id}

        mirror_url, initial_path = path_from_mirror_url({url}, {path})

        def policy(content, path):
            if initial_path.endswith('sjson'):
                return read_signed(content)
            else:
                return content

        config = {{'max_items': {max}, 'keep': False, 'cloud_name': {cloud_name}
                   'content_id': 'auto.sync'}}
        smirror = UrlMirrorReader(mirror_url, policy=policy)

        # juju looks in simplestreams/data/* in swift to figure out which
        # images to deploy, so this path isn't really configurable even though
        # it is.
        store = SwiftObjectStore('simplestreams/data/')

        tmirror = glance.GlanceMirror(config=config, objectstore=store)
        tmirror.sync(smirror, path=initial_path)
    """)


@hooks.hook('identity-service-relation-changed')
def identity_service_changed():
    """ Create / update sync script template when ID service changes
    TODOs:
    - handle other ID service hook events
    """
    id_context = IdentityServiceContext()
    id_dict = id_context()
    program_template = glance_sync_program("FIXME_URL")
    program = program_template.format(**id_dict)
    log("Template sync program is '{program}'".format(program=program))


@hooks.hook('install')
def install():
    apt_install(packages=['python-simplestreams', 'python-glanceclient',
                          'python-swiftclient', 'ubuntu-cloudimage-keyring'])
    log('end install hook.')


@hooks.hook('config-changed')
def config_changed():
    log('begin config-changed hook.')

    # TODO: actually install the cron job :-)
    if not config('run'):
        log('run not enabled, uninstalling cronjob and exiting')
        return
    else:
        log('installing a cron job and running a manual sync')


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))

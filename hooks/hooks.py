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

from simplestreams.mirror import glance
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


hooks = Hooks()


def glance_sync_program(sstream_url, max_, ):
    # Here things in {}s are variables, and dictionaries. obviously broken, but
    # it's a start.

    # TODOs:
    #   - We might want to allow people to set regions as well, so
    #     you can have one charm sync to one region, instead of doing a cross
    #     region sync.
    #   - allow people to specify their own policy, since they can specify
    #     their own mirrors.
    #   - potentially allow people to specify backup mirrors?
    return dedent("""
        import os
        from simplestreams.mirrors import glance, swift, UrlMirrorReader

        os.environ['OS_AUTH_URL'] = {auth_url}
        os.environ['OS_USERNAME'] = {auth_username}

        # could instead use OS_PASSWORD here, but presumably we want to get an
        # auth token via juju/keystone magic.
        os.environ['OS_AUTH_TOKEN'] = {auth_token}

        # could instead use OS_TENANT_ID here, i'm not sure which is better.
        os.environ['OS_TENANT_NAME'] = {auth_tenant_name}

        def policy(content, path):
            if args.path.endswith('sjson'):
                return util.read_signed(content, keyring=args.keyring)
            else:
                return content

        config = {'max_items': {max}, 'keep' False, 'cloud_name': {cloud_name}}
        smirror = UrlMirrorReader({url}, policy=policy)

        # juju looks in simplestreams/data/* in swift to figure out which
        # images to deploy, so this path isn't really configurable even though
        # it is.
        store = swift.SwiftObjectStore('simplestreams/data/')

        tmirror = glance.GlanceMirror(config=config, objectstore=store)
        tmirror.sync(smirror, path={path})
    """


@hooks.hook('install')
def install():
    apt_install(packages=['python-simplestreams', 'python-glanceclient',
                          'python-swiftclient', 'ubuntu-cloudimage-keyring'])
    log('end install hook.')


@hooks.hook('config_changed')
def config_changed():
    log('begin config_changed hook.')

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

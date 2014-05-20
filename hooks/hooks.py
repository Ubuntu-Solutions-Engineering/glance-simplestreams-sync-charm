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


@hooks.hook('install')
def install():
    apt_install(packages=['python-simplestreams', 'python-glanceclient',
                          'python-swiftclient'])
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

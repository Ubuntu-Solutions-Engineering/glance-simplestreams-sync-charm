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


def setup_logging():
    logfilename = '/var/log/glance-simplestreams-sync.log'
    h = logging.FileHandler(logfilename)
    h.setFormatter(logging.Formatter(
        '%(levelname)-9s * %(asctime)s [PID:%(process)d] * %(name)s * '
        '%(message)s',
        datefmt='%m-%d %H:%M:%S'))

    logger = logging.getLogger()
    logger.setLevel('DEBUG')
    logger.addHandler(h)

    return logger

log = setup_logging()


import atexit
import fcntl
import glanceclient
from keystoneclient.v2_0 import client as keystone_client
import keystoneclient.exceptions as keystone_exceptions
import kombu
import os
from simplestreams.mirrors import glance, UrlMirrorReader
from simplestreams.objectstores.swift import SwiftObjectStore
from simplestreams.util import read_signed, path_from_mirror_url
import sys
import time
import traceback
from urlparse import urlsplit
import yaml

KEYRING = '/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg'
CONF_FILE_DIR = '/etc/glance-simplestreams-sync'
PID_FILE_DIR = '/var/run'
CHARM_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'mirrors.yaml')
ID_CONF_FILE_NAME = os.path.join(CONF_FILE_DIR, 'identity.yaml')

SYNC_RUNNING_FLAG_FILE_NAME = os.path.join(PID_FILE_DIR,
                                           'glance-simplestreams-sync.pid')

# juju looks in simplestreams/data/* in swift to figure out which
# images to deploy, so this path isn't really configurable even though
# it is.
SWIFT_DATA_DIR = 'simplestreams/data/'

PRODUCT_STREAMS_SERVICE_NAME = 'image-stream'
PRODUCT_STREAMS_SERVICE_TYPE = 'product-streams'
PRODUCT_STREAMS_SERVICE_DESC = 'Ubuntu Product Streams'

CRON_POLL_FILENAME = '/etc/cron.d/glance_simplestreams_sync_fastpoll'

# TODOs:
#   - allow people to specify their own policy, since they can specify
#     their own mirrors.
#   - potentially allow people to specify backup mirrors?
#   - debug keyring support
#   - figure out what content_id is and whether we should allow users to
#     set it

try:
    from simplestreams.util import ProgressAggregator
    SIMPLESTREAMS_HAS_PROGRESS = True
except ImportError:
    class ProgressAggregator:
        "Dummy class to allow charm to load with old simplestreams"
    SIMPLESTREAMS_HAS_PROGRESS = False


class StatusMessageProgressAggregator(ProgressAggregator):
    def __init__(self, remaining_items, send_status_message):
        super(StatusMessageProgressAggregator, self).__init__(remaining_items)
        self.send_status_message = send_status_message

    def emit(self, progress):
        size = float(progress['size'])
        written = float(progress['written'])
        cur = self.total_image_count - len(self.remaining_items) + 1
        totpct = float(self.total_written) / self.total_size
        msg = "{name} {filepct:.0%}\n"\
              "({cur} of {tot} images) total: "\
              "{totpct:.0%}".format(name=progress['name'],
                                    filepct=(written / size),
                                    cur=cur,
                                    tot=self.total_image_count,
                                    totpct=totpct)
        self.send_status_message(dict(status="Syncing",
                                      message=msg))


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
    conf_files = [ID_CONF_FILE_NAME, CHARM_CONF_FILE_NAME]
    for conf_file_name in conf_files:
        if not os.path.exists(conf_file_name):
            log.info("{} does not exist, exiting.".format(conf_file_name))
            sys.exit(1)

    id_conf = read_conf(ID_CONF_FILE_NAME)
    if None in id_conf.values():
        log.info("Configuration value missing in {}:\n"
                 "{}".format(ID_CONF_FILE_NAME, id_conf))
        sys.exit(1)
    charm_conf = read_conf(CHARM_CONF_FILE_NAME)
    if None in charm_conf.values():
        log.info("Configuration value missing in {}:\n"
                 "{}".format(CHARM_CONF_FILE_NAME, charm_conf))
        sys.exit(1)

    return id_conf, charm_conf


def set_proxy_env(id_conf):
    for env in ['http_proxy', 'https_proxy', 'no_proxy']:
        if env not in id_conf:
            continue
        os.environ[env] = id_conf[env]
        os.environ[env.upper()] = id_conf[env]


def set_openstack_env(id_conf, charm_conf):
    auth_url = '%s://%s:%s/v2.0' % (id_conf['service_protocol'],
                                    id_conf['service_host'],
                                    id_conf['service_port'])
    os.environ['OS_AUTH_URL'] = auth_url
    os.environ['OS_USERNAME'] = id_conf['admin_user']
    os.environ['OS_PASSWORD'] = id_conf['admin_password']
    os.environ['OS_TENANT_ID'] = id_conf['admin_tenant_id']

    os.environ['OS_REGION_NAME'] = charm_conf['region']


def do_sync(charm_conf, status_exchange):

    for mirror_info in charm_conf['mirror_list']:
        mirror_url, initial_path = path_from_mirror_url(mirror_info['url'],
                                                        mirror_info['path'])

        log.info("configuring sync for url {}".format(mirror_info))

        smirror = UrlMirrorReader(mirror_url, policy=policy)

        if charm_conf['use_swift']:
            store = SwiftObjectStore(SWIFT_DATA_DIR)
        else:
            store = None

        content_id = charm_conf['content_id_template'].format(
            region=charm_conf['region'])

        config = {'max_items': mirror_info['max'],
                  'modify_hook': charm_conf['modify_hook_scripts'],
                  'keep_items': False,
                  'content_id': content_id,
                  'cloud_name': charm_conf['cloud_name'],
                  'item_filters': mirror_info['item_filters']}

        mirror_args = dict(config=config, objectstore=store,
                           name_prefix=charm_conf['name_prefix'])

        if SIMPLESTREAMS_HAS_PROGRESS:
            log.info("Calling DryRun mirror to get item list")

            drmirror = glance.ItemInfoDryRunMirror(config=config,
                                                   objectstore=store)
            drmirror.sync(smirror, path=initial_path)
            p = StatusMessageProgressAggregator(drmirror.items,
                                                status_exchange.send_message)
            mirror_args['progress_callback'] = p.progress_callback
        else:
            log.info("Detected simplestreams version without progress"
                     " update support. Only limited feedback available.")

        tmirror = glance.GlanceMirror(**mirror_args)

        log.info("calling GlanceMirror.sync")
        tmirror.sync(smirror, path=initial_path)


def update_product_streams_service(ksc, services, region):
    """
    Updates URLs of product-streams endpoint to point to swift URLs.
    """

    swift_services = [s for s in services
                      if s['name'] == 'swift']
    if len(swift_services) != 1:
        log.error("found {} swift services. expecting one."
                  " - not updating endpoint.".format(len(swift_services)))
        return

    swift_service_id = swift_services[0]['id']

    endpoints = [e._info for e in ksc.endpoints.list()
                 if e._info['region'] == region]

    swift_endpoints = [e for e in endpoints
                       if e['service_id'] == swift_service_id]
    if len(swift_endpoints) != 1:
        log.warning("found {} swift endpoints, expecting one - not"
                    " updating product-streams"
                    " endpoint.".format(len(swift_endpoints)))
        return

    swift_endpoint = swift_endpoints[0]

    ps_services = [s for s in services
                   if s['name'] == PRODUCT_STREAMS_SERVICE_NAME]
    if len(ps_services) != 1:
        log.error("found {} product-streams services. expecting one."
                  " - not updating endpoint.".format(len(ps_services)))
        return

    ps_service_id = ps_services[0]['id']

    ps_endpoints = [e for e in endpoints
                    if e['service_id'] == ps_service_id]

    if len(ps_endpoints) != 1:
        log.warning("found {} product-streams endpoints in region {},"
                    " expecting one - not updating"
                    " endpoint".format(ps_endpoints, region,
                                       len(ps_endpoints)))
        return

    log.info("Deleting existing product-streams endpoint: ")
    ksc.endpoints.delete(ps_endpoints[0]['id'])

    services_tenant_ids = [t.id for t in ksc.tenants.list()
                           if t.name == 'services']

    if len(services_tenant_ids) != 1:
        log.warning("found {} tenants named 'services',"
                    " expecting one. Not updating"
                    " endpoint".format(len(services_tenant_ids)))

    services_tenant_id = services_tenant_ids[0]

    path = "/v1/AUTH_{}/{}".format(services_tenant_id,
                                   SWIFT_DATA_DIR)

    swift_public_url = swift_endpoint['publicurl']
    sr_p = urlsplit(swift_public_url)
    ps_public_url = sr_p._replace(path=path).geturl()

    swift_internal_url = swift_endpoint['internalurl']
    sr_i = urlsplit(swift_internal_url)
    ps_internal_url = sr_i._replace(path=path).geturl()

    create_args = dict(region=region,
                       service_id=ps_service_id,
                       publicurl=ps_public_url,
                       adminurl=swift_endpoint['adminurl'],
                       internalurl=ps_internal_url)
    log.info("creating product-streams endpoint: {}".format(create_args))
    ksc.endpoints.create(**create_args)


class StatusExchange:
    """Wrapper for rabbitmq status exchange connection.

    If no connection exists, this attempts to create a connection
    before sending each message.
    """

    def __init__(self):
        self.conn = None
        self.exchange = None

        self._setup_connection()

    def _setup_connection(self):
        """Returns True if a valid connection exists already, or if one can be
        created."""

        if self.conn:
            return True

        id_conf = read_conf(ID_CONF_FILE_NAME)

        hosts = id_conf.get('rabbit_hosts', None)
        if hosts is not None:
            host = hosts[0]
        else:
            host = id_conf.get('rabbit_host', None)

        if host is None:
            log.warning("no host info in configuration, can't set up rabbit.")
            return False

        try:
            url = "amqp://{}:{}@{}/{}".format(id_conf['rabbit_userid'],
                                              id_conf['rabbit_password'],
                                              host,
                                              id_conf['rabbit_virtual_host'])

            self.conn = kombu.BrokerConnection(url)
            self.exchange = kombu.Exchange("glance-simplestreams-sync-status")
            status_queue = kombu.Queue("glance-simplestreams-sync-status",
                                       exchange=self.exchange)

            status_queue(self.conn.channel()).declare()

        except:
            log.exception("Exception during kombu setup")
            return False

        return True

    def send_message(self, msg):
        if not self._setup_connection():
            log.warning("No rabbitmq connection available for msg"
                        "{}. Message will be lost.".format(str(msg)))
            return

        with self.conn.Producer(exchange=self.exchange) as producer:
            producer.publish(msg)

    def close(self):
        if self.conn:
            self.conn.close()


def cleanup():
    try:
        os.unlink(SYNC_RUNNING_FLAG_FILE_NAME)
    except OSError as e:
        if e.errno != 2:
            raise e


def main():

    log.info("glance-simplestreams-sync started.")

    atexit.register(cleanup)

    lockfile = open(SYNC_RUNNING_FLAG_FILE_NAME, 'w')
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log.info("{} is locked, exiting".format(SYNC_RUNNING_FLAG_FILE_NAME))
        sys.exit(0)

    lockfile.write(str(os.getpid()))

    id_conf, charm_conf = get_conf()

    set_proxy_env(id_conf)

    set_openstack_env(id_conf, charm_conf)

    ksc = keystone_client.Client(username=os.environ['OS_USERNAME'],
                                 password=os.environ['OS_PASSWORD'],
                                 tenant_id=os.environ['OS_TENANT_ID'],
                                 auth_url=os.environ['OS_AUTH_URL'])

    services = [s._info for s in ksc.services.list()]
    servicenames = [s['name'] for s in services]
    ps_service_exists = PRODUCT_STREAMS_SERVICE_NAME in servicenames
    swift_exists = 'swift' in servicenames

    log.info("ps_service_exists={}, charm_conf['use_swift']={}"
             ", swift_exists={}".format(ps_service_exists,
                                        charm_conf['use_swift'],
                                        swift_exists))

    if ps_service_exists and charm_conf['use_swift'] and swift_exists:
        log.info("Updating product streams service.")
        try:
            update_product_streams_service(ksc, services,
                                           charm_conf['region'])
        except:
            log.exception("Exception during update_product_streams_service")

    else:
        log.info("Not updating product streams service.")

    should_delete_cron_poll = True

    status_exchange = StatusExchange()

    try:
        log.info("Beginning image sync")

        status_exchange.send_message({"status": "Started",
                                      "message": "Sync starting."})
        do_sync(charm_conf, status_exchange)
        ts = time.strftime("%x %X")
        completed_msg = "Sync completed at {}".format(ts)
        status_exchange.send_message({"status": "Done",
                                      "message": completed_msg})

    except keystone_exceptions.EndpointNotFound as e:
        # matching string "{PublicURL} endpoint for {type}{region} not
        # found".  where {type} is 'image' and {region} is potentially
        # not empty so we only match on this substring:
        if 'endpoint for image' in e.message:
            should_delete_cron_poll = False
            log.info("Glance endpoint not found, will continue polling.")

    except glanceclient.exc.ClientException as e:
        log.exception("Glance Client exception during do_sync."
                      " will continue polling.")
        should_delete_cron_poll = False

    except Exception as e:
        log.exception("Exception during do_sync")
        status_exchange.send_message({"status": "Error",
                                      "message": traceback.format_exc()})

    status_exchange.close()

    if os.path.exists(CRON_POLL_FILENAME) and should_delete_cron_poll:
        os.unlink(CRON_POLL_FILENAME)
        log.info("Initial sync attempt done. every-minute cronjob removed.")
    log.info("sync done.")


if __name__ == "__main__":
    main()

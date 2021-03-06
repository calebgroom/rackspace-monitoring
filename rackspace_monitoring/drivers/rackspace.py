# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import httplib
import urlparse

try:
    import simplejson as json
except:
    import json

from libcloud.common.types import MalformedResponseError, LibcloudError
from libcloud.common.types import LazyList
from libcloud.common.base import Response

from rackspace_monitoring.providers import Provider
from rackspace_monitoring.utils import to_underscore_separated

from rackspace_monitoring.base import (MonitoringDriver, Entity,
                                      NotificationPlan, MonitoringZone,
                                      Notification, CheckType, Alarm, Check,
                                      NotificationType, AlarmChangelog)

from libcloud.common.rackspace import AUTH_URL_US
from libcloud.common.openstack import OpenStackBaseConnection

API_VERSION = 'v1.0'
API_URL = 'https://cmbeta.api.rackspacecloud.com/%s' % (API_VERSION)

class RackspaceMonitoringValidationError(LibcloudError):

    def __init__(self, code, type, message, details, driver):
        self.code = code
        self.type = type
        self.message = message
        self.details = details
        super(RackspaceMonitoringValidationError, self).__init__(value=message,
                                                                 driver=driver)

    def __repr__(self):
        string = '<ValidationError type=%s, ' % (self.type)
        string += 'message="%s", details=%s>' % (self.message, self.details)
        return string


class LatestAlarmState(object):
    def __init__(self, entity_id, check_id, alarm_id, timestamp, state):
        self.entity_id = entity_id
        self.check_id = check_id
        self.alarm_id = alarm_id
        self.timestamp = timestamp
        self.state = state

    def __repr__(self):
        return ('<LatestAlarmState: entity_id=%s, check_id=%s, alarm_id=%s, '
                'state=%s ...>' %
                (self.entity_id, self.check_id, self.alarm_id, self.state))


class RackspaceMonitoringResponse(Response):

    valid_response_codes = [httplib.CONFLICT]

    def success(self):
        i = int(self.status)
        return i >= 200 and i <= 299 or i in self.valid_response_codes

    def parse_body(self):
        if not self.body:
            return None

        if 'content-type' in self.headers:
            key = 'content-type'
        elif 'Content-Type' in self.headers:
            key = 'Content-Type'
        else:
            raise LibcloudError('Missing content-type header')

        content_type = self.headers[key]
        if content_type.find(';') != -1:
            content_type = content_type.split(';')[0]

        if content_type == 'application/json':
            try:
                data = json.loads(self.body)
            except:
                raise MalformedResponseError('Failed to parse JSON',
                                             body=self.body,
                                             driver=RackspaceMonitoringDriver)
        elif content_type == 'text/plain':
            data = self.body
        else:
            data = self.body

        return data

    def parse_error(self):
        body = self.parse_body()
        if self.status == httplib.BAD_REQUEST:
            error = RackspaceMonitoringValidationError(message=body['message'],
                                               code=body['code'],
                                               type=body['type'],
                                               details=body['details'],
                                               driver=self.connection.driver)
            raise error

        return body


class RackspaceMonitoringConnection(OpenStackBaseConnection):
    """
    Base connection class for the Rackspace Monitoring driver.
    """

    type = Provider.RACKSPACE
    responseCls = RackspaceMonitoringResponse
    auth_url = AUTH_URL_US
    _url_key = "monitoring_url"

    def __init__(self, user_id, key, secure=False, ex_force_base_url=API_URL,
                 ex_force_auth_url=None, ex_force_auth_version='2.0'):
        self.api_version = API_VERSION
        self.monitoring_url = ex_force_base_url
        self.accept_format = 'application/json'
        super(RackspaceMonitoringConnection, self).__init__(user_id, key,
                                secure=secure,
                                ex_force_base_url=ex_force_base_url,
                                ex_force_auth_url=ex_force_auth_url,
                                ex_force_auth_version=ex_force_auth_version)

    def request(self, action, params=None, data='', headers=None, method='GET',
                raw=False):
        if not headers:
            headers = {}
        if not params:
            params = {}

        headers['Accept'] = 'application/json'

        if method in ['POST', 'PUT']:
            headers['Content-Type'] = 'application/json; charset=UTF-8'
            data = json.dumps(data)

        return super(RackspaceMonitoringConnection, self).request(
            action=action,
            params=params, data=data,
            method=method, headers=headers,
            raw=raw
        )


class RackspaceMonitoringDriver(MonitoringDriver):
    """
    Base Rackspace Monitoring driver.

    """
    name = 'Rackspace Monitoring'
    connectionCls = RackspaceMonitoringConnection

    def __init__(self, *args, **kwargs):
        self._ex_force_base_url = kwargs.pop('ex_force_base_url', None)
        self._ex_force_auth_url = kwargs.pop('ex_force_auth_url', None)
        self._ex_force_auth_version = kwargs.pop('ex_force_auth_version', None)
        super(RackspaceMonitoringDriver, self).__init__(*args, **kwargs)

        self.connection._populate_hosts_and_request_paths()
        tenant_id = self.connection.tenant_ids['compute']
        self.connection._force_base_url = '%s/%s' % (
                self.connection._force_base_url, tenant_id)

    def _ex_connection_class_kwargs(self):
        rv = {}
        if self._ex_force_base_url:
            rv['ex_force_base_url'] = self._ex_force_base_url
        if self._ex_force_auth_url:
            rv['ex_force_auth_url'] = self._ex_force_auth_url
        if self._ex_force_auth_version:
            rv['ex_force_auth_version'] = self._ex_force_auth_version
        return rv

    def _get_more(self, last_key, value_dict):
        key = None

        params = value_dict.get('params', {})

        if not last_key:
            key = value_dict.get('start_marker')
        else:
            key = last_key

        if key:
            params['marker'] = key

        response = self.connection.request(value_dict['url'], params)

        # newdata, self._last_key, self._exhausted
        if response.status == httplib.NO_CONTENT:
            return [], None, False
        elif response.status == httplib.OK:
            resp = json.loads(response.body)
            l = None

            if 'list_item_mapper' in value_dict:
                func = value_dict['list_item_mapper']
                l = [func(x, value_dict) for x in resp['values']]
            else:
                l = value_dict['object_mapper'](resp, value_dict)
            m = resp['metadata'].get('next_marker')
            return l, m, m == None

        body = json.loads(response.body)

        details = body['details'] if 'details' in body else ''
        raise LibcloudError('Unexpected status code: %s (url=%s, details=%s)' %
                            (response.status, value_dict['url'], details))

    def _plural_to_singular(self, name):
        kv = {'entities': 'entity',
              'alarms': 'alarm',
              'checks': 'check',
              'notifications': 'notification',
              'notification_plans': 'notificationPlan'}

        return kv[name]

    def _url_to_obj_ids(self, url):
        rv = {}
        rp = self.connection.request_path
        path = urlparse.urlparse(url).path

        if path.startswith(rp):
            # remove version string stuff
            path = path[len(rp):]

        chunks = path.split('/')[1:]

        for i in range(0, len(chunks), 2):
            key = self._plural_to_singular(chunks[i]) + '_id'
            key = to_underscore_separated(key)
            rv[key] = chunks[i + 1]

        return rv

    def _create(self, url, data, coerce):
        for k in data.keys():
            if data[k] == None:
                del data[k]

        resp = self.connection.request(url,
                                       method='POST',
                                       data=data)
        if resp.status == httplib.CREATED:
            location = resp.headers.get('location')
            if not location:
                raise LibcloudError('Missing location header')
            obj_ids = self._url_to_obj_ids(location)
            return coerce(**obj_ids)
        else:
            raise LibcloudError('Unexpected status code: %s' % (resp.status))

    def _update(self, url, data, coerce):
        for k in data.keys():
            if data[k] == None:
                del data[k]

        resp = self.connection.request(url, method='PUT', data=data)
        if resp.status == httplib.NO_CONTENT:
            # location
            # /v1.0/{object_type}/{id}
            location = resp.headers.get('location')
            if not location:
                raise LibcloudError('Missing location header')

            obj_ids = self._url_to_obj_ids(location)
            return coerce(**obj_ids)
        else:
            raise LibcloudError('Unexpected status code: %s' % (resp.status))

    def list_check_types(self):
        value_dict = {'url': '/check_types',
                       'list_item_mapper': self._to_check_type}

        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def _to_check_type(self, obj, value_dict):
        return CheckType(id=obj['id'],
                         fields=obj.get('fields', []),
                         is_remote=obj.get('type') == 'remote')

    def list_notification_types(self):
        value_dict = {'url': '/notification_types',
                       'list_item_mapper': self._to_notification_type}

        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def _to_notification_type(self, obj, value_dict):
        return NotificationType(id=obj['id'],
                         fields=obj.get('fields', []))

    def _to_monitoring_zone(self, obj, value_dict):
        return MonitoringZone(id=obj['id'], label=obj['label'],
                              country_code=obj['country_code'],
                              source_ips=obj['source_ips'],
                              driver=self)

    def list_monitoring_zones(self):
        value_dict = {'url': '/monitoring_zones',
                       'list_item_mapper': self._to_monitoring_zone}
        return LazyList(get_more=self._get_more, value_dict=value_dict)

    ##########
    ## Alarms
    ##########

    def get_alarm(self, entity_id, alarm_id):
        url = "/entities/%s/alarms/%s" % (entity_id, alarm_id)
        resp = self.connection.request(url)
        return self._to_alarm(resp.object, {'entity_id': entity_id})

    def _to_alarm(self, alarm, value_dict):
        return Alarm(id=alarm['id'], type=alarm['check_type'],
            criteria=alarm['criteria'],
            notification_plan_id=alarm['notification_plan_id'],
            driver=self, entity_id=value_dict['entity_id'])

    def list_alarms(self, entity, ex_next_marker=None):
        value_dict = {'url': '/entities/%s/alarms' % (entity.id),
                      'start_marker': ex_next_marker,
                      'list_item_mapper': self._to_alarm,
                      'entity_id': entity.id}

        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def list_alarm_changelog(self, ex_next_marker=None):
        value_dict = {'url': '/changelogs/alarms',
                      'start_marker': ex_next_marker,
                      'list_item_mapper': self._to_alarm_changelog}

        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def _to_alarm_changelog(self, values, value_dict):
        alarm_changelog = AlarmChangelog(id=values['id'],
                                         alarm_id=values['alarm_id'],
                                         entity_id=values['entity_id'],
                                         check_id=values['check_id'],
                                         state=values['state'])
        return alarm_changelog

    def delete_alarm(self, alarm):
        resp = self.connection.request("/entities/%s/alarms/%s" % (
            alarm.entity_id, alarm.id),
            method='DELETE')
        return resp.status == httplib.NO_CONTENT

    def update_alarm(self, alarm, data):
        return self._update("/entities/%s/alarms/%s" % (alarm.entity_id,
                                                        alarm.id),
            data=data, coerce=self.get_alarm)

    def create_alarm(self, entity, **kwargs):
        data = {'check_type': kwargs.get('check_type'),
                'check_id': kwargs.get('check_id'),
                'criteria': kwargs.get('criteria'),
                'notification_plan_id': kwargs.get('notification_plan_id')}

        return self._create("/entities/%s/alarms" % (entity.id),
            data=data, coerce=self.get_alarm)

    def test_alarm(self, entity, **kwargs):
        data = {'criteria': kwargs.get('criteria'),
                'check_data': kwargs.get('check_data')}
        resp = self.connection.request("/entities/%s/test-alarm" % (entity.id),
                                       method='POST',
                                       data=data)
        return resp.object

    ####################
    ## Notifications
    ####################

    def list_notifications(self, ex_next_marker=None):
        value_dict = {'url': '/notifications',
                      'start_marker': ex_next_marker,
                      'list_item_mapper': self._to_notification}

        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def _to_notification(self, notification, value_dict):
        return Notification(id=notification['id'], label=notification['label'],
                            type=notification['type'],
                            details=notification['details'], driver=self)

    def get_notification(self, notification_id):
        resp = self.connection.request("/notifications/%s" % (notification_id))

        return self._to_notification(resp.object, {})

    def delete_notification(self, notification):
        resp = self.connection.request("/notifications/%s" % (notification.id),
                                       method='DELETE')
        return resp.status == httplib.NO_CONTENT

    def update_notification(self, notification, data):
        return self._update("/notifications/%s" % (notification.id),
            data=data, coerce=self.get_notification)

    def create_notification(self, **kwargs):
        data = {'label': kwargs.get('label'),
                'type': kwargs.get('type'),
                'details': kwargs.get('details')}

        return self._create("/notifications", data=data,
                            coerce=self.get_notification)

    ####################
    ## Notification Plan
    ####################

    def _to_notification_plan(self, notification_plan, value_dict):
        critical_state = notification_plan.get('critical_state', [])
        warning_state = notification_plan.get('warning_state', [])
        ok_state = notification_plan.get('ok_state', [])
        return NotificationPlan(id=notification_plan['id'],
            label=notification_plan['label'],
            critical_state=critical_state, warning_state=warning_state,
            ok_state=ok_state, driver=self)

    def get_notification_plan(self, notification_plan_id):
        resp = self.connection.request("/notification_plans/%s" % (
            notification_plan_id))
        return self._to_notification_plan(resp.object, {})

    def delete_notification_plan(self, notification_plan):
        resp = self.connection.request("/notification_plans/%s" %
                (notification_plan.id), method='DELETE')
        return resp.status == httplib.NO_CONTENT

    def list_notification_plans(self, ex_next_marker=None):
        value_dict = {'url': "/notification_plans",
                      'start_marker': ex_next_marker,
                      'list_item_mapper': self._to_notification_plan}
        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def update_notification_plan(self, notification_plan, data):
        return self._update("/notification_plans/%s" % (notification_plan.id),
            data=data,
            coerce=self.get_notification_plan)

    def create_notification_plan(self, **kwargs):
        data = {'label': kwargs.get('label'),
                'critical_state': kwargs.get('critical_state', []),
                'warning_state': kwargs.get('warning_state', []),
                'ok_state': kwargs.get('ok_state', []),
                }
        return self._create("/notification_plans", data=data,
                            coerce=self.get_notification_plan)

    ###########
    ## Checks
    ###########

    def get_check(self, entity_id, check_id):
        resp = self.connection.request('/entities/%s/checks/%s' % (entity_id,
                                                                   check_id))
        return self._to_check(resp.object, {'entity_id': entity_id})

    def _to_check(self, obj, value_dict):
        return Check(**{
            'id': obj['id'],
            'label': obj.get('label'),
            'timeout': obj['timeout'],
            'period': obj['period'],
            'monitoring_zones': obj['monitoring_zones_poll'],
            'target_alias': obj['target_alias'],
            'target_resolver': obj['target_resolver'],
            'type': obj['type'],
            'details': obj['details'],
            'driver': self,
            'entity_id': value_dict['entity_id']})

    def list_checks(self, entity, ex_next_marker=None):
        value_dict = {'url': "/entities/%s/checks" % (entity.id),
                      'start_marker': ex_next_marker,
                      'list_item_mapper': self._to_check,
                      'entity_id': entity.id}
        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def _check_kwarg_to_data(self, kwargs):
        data = {'who': kwargs.get('who'),
                'why': kwargs.get('why'),
                'label': kwargs.get('label'),
                'timeout': kwargs.get('timeout', 29),
                'period': kwargs.get('period', 30),
                "monitoring_zones_poll": kwargs.get('monitoring_zones', []),
                "target_alias": kwargs.get('target_alias'),
                "target_resolver": kwargs.get('target_resolver'),
                'type': kwargs.get('type'),
                'details': kwargs.get('details'),
                }

        for k in data.keys():
            if data[k] == None:
                del data[k]

        return data

    def test_check(self, entity, **kwargs):
        data = self._check_kwarg_to_data(kwargs)
        resp = self.connection.request("/entities/%s/test-check" % (entity.id),
                                       method='POST',
                                       data=data)
        return resp.object

    def create_check(self, entity, **kwargs):
        data = self._check_kwarg_to_data(kwargs)
        return self._create("/entities/%s/checks" % (entity.id),
            data=data, coerce=self.get_check)

    def update_check(self, check, data):
        return self._update("/entities/%s/checks/%s" % (check.entity_id,
                                                        check.id),
            data=data, coerce=self.get_check)

    def delete_check(self, check):
        resp = self.connection.request("/entities/%s/checks/%s" %
                                       (check.entity_id, check.id),
                                       method='DELETE')
        return resp.status == httplib.NO_CONTENT

    ###########
    ## Entity
    ###########

    def get_entity(self, entity_id):
        resp = self.connection.request("/entities/%s" % (entity_id))
        return self._to_entity(resp.object, {})

    def _to_entity(self, entity, value_dict):
        ips = []
        ipaddrs = entity.get('ip_addresses', {})
        if ipaddrs is not None:
            for key in ipaddrs.keys():
                ips.append((key, ipaddrs[key]))
        return Entity(id=entity['id'], label=entity['label'],
                      extra=entity['metadata'], driver=self, ip_addresses=ips)

    def delete_entity(self, entity, ex_delete_children=False):
        try:
            resp = self.connection.request("/entities/%s" % (entity.id),
                                           method='DELETE')
        except RackspaceMonitoringValidationError, e:
            type = e.details['type']
            if not ex_delete_children or e.type != 'childrenExistError':
                raise e

            if type == 'Check':
                self.ex_delete_checks(entity=entity)
            elif type == 'Alarm':
                self.ex_delete_alarms(entity=entity)

            return self.delete_entity(entity=entity, ex_delete_children=True)

        return resp.status == httplib.NO_CONTENT

    def list_entities(self, ex_next_marker=None):
        value_dict = {'url': '/entities',
                      'start_marker': ex_next_marker,
                      'list_item_mapper': self._to_entity}

        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def create_entity(self, **kwargs):
        data = {'who': kwargs.get('who'),
                'why': kwargs.get('why'),
                'ip_addresses': kwargs.get('ip_addresses', {}),
                'label': kwargs.get('label'),
                'metadata': kwargs.get('extra', {})}

        return self._create("/entities", data=data, coerce=self.get_entity)

    def update_entity(self, entity, data):
        return self._update("/entities/%s" % (entity.id),
            data=data, coerce=self.get_entity)

    def usage(self):
        resp = self.connection.request("/usage")
        return resp.object

    def _to_audit(self, audit, value_dict):
        return audit

    def list_audits(self, start_from=None, to=None):
        # TODO: add start/end date support
        value_dict = {'url': '/audits',
                      'params': {'limit': 200},
                      'list_item_mapper': self._to_audit}

        return LazyList(get_more=self._get_more, value_dict=value_dict)

    #########
    ## Other
    #########

    def test_check_and_alarm(self, entity, criteria, **kwargs):
        check_data = self.test_check(entity=entity, **kwargs)
        data = {'criteria': criteria, 'check_data': check_data}
        result = self.test_alarm(entity=entity, **data)
        return result

    ####################
    # Extension methods
    ####################

    def ex_list_alarm_history_checks(self, entity, alarm):
        resp = self.connection.request('/entities/%s/alarms/%s/history' %
                                       (entity.id, alarm.id)).object
        return resp

    def ex_list_alarm_history(self, entity, alarm, check, ex_next_marker=None):
        value_dict = {'url': '/entities/%s/alarms/%s/history/%s' %
                              (entity.id, alarm.id, check.id),
                       'list_item_mapper': self._to_alarm_history_obj}
        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def _to_alarm_history_obj(self, values, value_dict):
        return values

    def ex_delete_checks(self, entity):
        # Delete all Checks for an entity
        checks = self.list_checks(entity=entity)
        for check in checks:
            self.delete_check(check=check)

    def ex_delete_alarms(self, entity):
        # Delete all Alarms for an entity
        alarms = self.list_alarms(entity=entity)
        for alarm in alarms:
            self.delete_alarm(alarm=alarm)

    def ex_limits(self):
        resp = self.connection.request('/limits',
                                       method='GET')
        return resp.object

    def ex_views_overview(self, ex_next_marker=None):
        value_dict = {'url': '/views/overview',
                      'start_marker': ex_next_marker,
                      'list_item_mapper': self._to_overview_obj}

        return LazyList(get_more=self._get_more, value_dict=value_dict)

    def _to_latest_alarm_state(self, obj, value_dict):
        return LatestAlarmState(entity_id=obj['entity_id'],
                check_id=obj['check_id'], alarm_id=obj['alarm_id'],
                timestamp=obj['timestamp'], state=obj['state'])

    def _to_overview_obj(self, data, value_dict):
        entity = self._to_entity(data['entity'], {})

        child_value_dict = {'entity_id': entity.id}
        checks = [self._to_check(check, child_value_dict) for check
                  in data['checks']]
        alarms = [self._to_alarm(alarm, child_value_dict) for alarm
                  in data['alarms']]
        latest_alarm_states = [self._to_latest_alarm_state(item, {}) for item
                               in data['latest_alarm_states']]

        obj = {'entity': entity, 'checks': checks, 'alarms': alarms,
               'latest_alarm_states': latest_alarm_states}
        return obj

# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Derives a spinnaker subsystem spring configuration."""

import logging
import re
import time
import socket
import urllib2
from json import JSONDecoder

from .expression_dict import ExpressionDict


def infer(json):
  """Infer the configuration from the json document.

  This ordering is to mimick
  https://docs.spring.io/spring-boot/docs/current/reference/html/boot-features-external-config.html

  Args:
    json: [dict] A JSON document specifying the configuration.
        This is assumed to be the result of spinnaker system '/env' URLs
        that return the configuration information that spring uses.
  """
  # Build a dictionary keyed by the different config sources so
  # that we can look them up later.
  application_config = {}
  for name, value in json.items():
    match = re.match(r'applicationConfig: \[(.+)\](.*)', name)
    if not match:
      continue
    key = match.group(1)
    decorator = match.group(2) or ''
    application_config[key + decorator] = value

  # Load global properties into the dictionary.
  # The ordering here is lower to higher precedence.
  expr_dict = ExpressionDict()
  expr_dict.update(json.get('defaultProperties', {}))
  expr_dict.update(json.get('systemProperties', {}))
  expr_dict.update(json.get('systemEnvironment', {}))

  names = expr_dict['spring.config.name'].split(',')
  profiles = expr_dict['spring.profiles.active'].split(',')
  locations = expr_dict['spring.config.location'].split(',')

  # Mix in the different profile locations.
  #
  # TODO(ewiseblatt):
  # Actually, this is wrong. We only want one profile.
  for location in locations:
    location_names = names if location.endswith('/') else ['']
    for name in location_names:
        # pylint: disable=bad-indentation
        root_filename = 'file:{location}{name}'.format(
            location=location, name=name)
        expr_dict.update(
            application_config.get(root_filename + '.yml', {}))
        for profile in profiles:
            key = root_filename + '-' + profile + '.yml'
            expr_dict.update(application_config.get(key, {}))

  return expr_dict


def scrape_spring_config(url, timeout=60, empty_if_404=True):
  """Construct a config binding dictionary from a running instance's baseUrl.

  Args:
    url: The url to construct from.
    empty_if_404: With spring boot 1.5.4 resovledEnv is not visible by default.
                  If True then tolerate this treating a 404 as being empty.

  Raises:
    urlib2.URLError if url is bad.
  """
  request = urllib2.Request(url=url)
  final_time = time.time() + timeout
  while True:
    # Sometimes this is not yet ready, so allow retries
    try:
      response = urllib2.urlopen(request, timeout=min(10, timeout))
      break
    except socket.timeout as ex:
      logging.info('Failed to scrape %s -- try again in 1s: %s', url, ex)
      time.sleep(1)
    except urllib2.HTTPError as ex:
      if ex.code == 404:
        logging.warning('Could not scrape config from url=%s: %s'
                        '\n  Suppressing this error and returning empty results.',
                        url, ex)
        return {}
      else:
        logging.exception('Could not scrape config from url=%s: %s'
                          '\n  Consider reconfiguring the server with: '
                          ' management.security.enabled: false',
                          url, ex)
        raise
    except urllib2.URLError as ex:
      logging.exception('Could not scrape config from url=%s: %s', url, ex)
      raise

  http_code = response.getcode()
  content = response.read()
  if http_code < 200 or http_code >= 300:
    raise ValueError('Invalid HTTP={code} from {url}:\n{msg}'.format(
        code=http_code, url=url, msg=content))
  return JSONDecoder().decode(content)

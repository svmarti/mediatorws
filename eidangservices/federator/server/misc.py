# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# This is <misc.py>
# -----------------------------------------------------------------------------
# This file is part of EIDA NG webservices (eida-federator).
#
# eida-federator is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# eida-federator is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# ----
#
# Copyright (c) Daniel Armbruster (ETH), Fabian Euchner (ETH)
#
# REVISION AND CHANGES
# 2018/05/28        V0.1    Daniel Armbruster
# -----------------------------------------------------------------------------
"""
Miscellaneous utils.
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from builtins import * # noqa

import logging
import os
import random
import tempfile

from eidangservices import settings, utils
from eidangservices.federator import __version__
from eidangservices.utils.httperrors import FDSNHTTPError
from eidangservices.utils.request import (binary_request, RequestsError,
                                          NoContent)
from eidangservices.utils.sncl import StreamEpoch


def get_temp_filepath():
    """Return path of temporary file."""

    return os.path.join(
        tempfile.gettempdir(), next(tempfile._get_candidate_names()))

# get_temp_filepath ()

def choices(seq, k=1):
    return ''.join(random.choice(seq) for i in range(k))

# choices ()

def elements_equal(e, e_other, exclude_tags=[], recursive=True):
    """
    Compare XML :py:class:`lxml.etree` elements.

    :param e: :py:class:`lxml.etree` to compare with :code:`e_other`.
    :type e: :py:class:`lxml.etree`
    :type e_other: :py:class:`lxml.etree`
    :param list exclude_tags: List of child element tags to be excluded
        while comparing. When excluding child elements the function
        makes use of :py:func:`copy.deepcopy`
    :param bool recursive: Recursively exclude matching child elements.

    .. note:: The function expects child elements to be ordered.
    """
    local_e = e
    local_e_other = e_other

    def remove_elements(t, exclude_tags, recursive):
        for tag in exclude_tags:
            xpath = tag
            if recursive:
                xpath = ".//{}".format(tag)
            for n in t.findall(xpath):
                n.getparent().remove(n)

    if exclude_tags:
        # XXX(damb): In order to make use of len(e) to increase
        # performance we create local copies of the elements with child
        # elements excluded
        from copy import deepcopy
        local_e = deepcopy(e)
        local_e_other = deepcopy(e_other)
        remove_elements(local_e, exclude_tags, recursive)
        remove_elements(local_e_other, exclude_tags, recursive)

    if local_e.tag != local_e_other.tag:
        return False
    if local_e.text != local_e_other.text:
        return False
    if local_e.tail != local_e_other.tail:
        return False
    if local_e.attrib != local_e_other.attrib:
        return False
    if len(local_e) != len(local_e_other):
        return False
    return all(elements_equal(c, c_other)
               for c, c_other in zip(local_e, local_e_other))

# elements_equal ()

def route(req, default_endtime=None,
          nodata=settings.FDSN_DEFAULT_NO_CONTENT_ERROR_CODE):
    """
    Create the routing table.

    :param req: Request object for the routing service used
    :type req: :py:class:`requests.Request`
    :param default_endtime: Default endtime to be used if the routing service
        returns an empty value
    :type default_endtime: None or :py:class:`datetime.datetime`
    :param int nodata: HTTP status code used during exception handling if the
        routing service returns no data
    """
    logger = logging.getLogger('flask.app.federator.misc')

    routing_table = []

    try:
        with binary_request(req) as fd:
            # parse the routing service's output stream; create a routing
            # table
            urlline = None
            stream_epochs = []

            while True:
                line = fd.readline()

                if not urlline:
                    urlline = line.strip()
                elif not line.strip():
                    # set up the routing table
                    if stream_epochs:
                        routing_table.append(
                            utils.Route(url=urlline,
                                        streams=stream_epochs))
                    urlline = None
                    stream_epochs = []

                    if not line:
                        break
                else:
                    stream_epochs.append(
                        StreamEpoch.from_snclline(
                            line, default_endtime))

    except NoContent as err:
        logger.warning(err)
        raise FDSNHTTPError.create(nodata)
    except RequestsError as err:
        logger.error(err)
        raise FDSNHTTPError.create(500, service_version=__version__)
    else:
        logger.debug(
            'Number of routes received: {}'.format(len(routing_table)))

    return routing_table

# route ()

# ---- END OF <misc.py> ----

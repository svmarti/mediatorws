# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# This is <auth.py>
# -----------------------------------------------------------------------------
#
# This file is part of EIDA NG webservices (eida-federator)
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
# 2018/12/01        V0.1    Daniel Armbruster
# =============================================================================
"""
federator access and authentication facilities
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from builtins import * # noqa

import datetime
import logging

from flask import current_app
from requests_oauthlib import OAuth2

from eidangservices import settings, utils
from eidangservices.federator.server.misc import (
    route as route_with_stationlite)
from eidangservices.federator.server.request import RoutingRequestHandler
from eidangservices.utils.httperrors import NoDataError


class AuthMixin(object):
    """
    Mixin allowing access and authentication for
    concrete :py:class:RequestProcessor` class implementations.

    :param auth: Authentication object
    :type auth: :py:class:`requests.auth.AuthBase`
    """

    LOGGER = 'flask.app.federator.auth'

    def __init__(self, **kwargs):
        super().__init__()
        for k in ('auth', 'query_params', 'stream_epochs', 'post'):
            if kwargs.get(k) is None:
                raise TypeError(
                    "Missing required argument: {!r}".format(k))

        self.query_params = kwargs['query_params']
        self.stream_epochs = kwargs['stream_epochs']
        self.post = kwargs['post']

        self.auth = kwargs['auth']

        self._routing_service = current_app.config['ROUTING_SERVICE']

        self.logger = logging.getLogger(self.LOGGER)

    # __init__ ()

    @property
    def token(self):
        return (self.auth if isinstance(self.auth, OAuth2) else None)

    def __route(self, request_handler, default_endtime):
        """
        Utility function used for routing.

        :param request_handler: Request handler to be used making the request
        :type request_handler: :py:class:`RoutingRequestHandler`
        """
        self.logger.info(
            "Fetching routes from {!r}".format(request_handler.url))

        return route_with_stationlite(
            (request_handler.post() if self.post else request_handler.get()),
            default_endtime=default_endtime,
            nodata=int(self.query_params.get(
                'nodata',
                settings.FDSN_DEFAULT_NO_CONTENT_ERROR_CODE)))

    # __route ()

    def _route_closed(self, default_endtime=datetime.datetime.utcnow()):
        """
        Create the routing table using the routing service provided.
        """
        rh = RoutingRequestHandler(self._routing_service, self.query_params,
                                   self.stream_epochs, access='closed')

        return [utils.AuthRoute.from_route(r)
                for r in self.__route(rh, default_endtime)]

    # _route_closed ()

    def _route_open(self, default_endtime=datetime.datetime.utcnow()):
        """
        Create the routing table using the routing service provided.
        """
        rh = RoutingRequestHandler(
            self._routing_service, self.query_params,
            self.stream_epochs, access='open')

        return self.__route(rh, default_endtime)

    # _route_closed ()

    def _route(self, default_endtime):
        """
        Create the routing tables using the routing service provided.

        :param default_endtime: Endtime to be used if stream epochs do not ship
            one
        :type default_endtime: :py:class:`datetime.datetime`

        :returns: :py:class:`tuple` of a routing table with *restricted* routes
            and a routing table with *open* routes.
        :rtype: tuple
        """
        routes_closed = []
        routes_open = []

        try:
            routes_closed = self._route_closed(default_endtime=default_endtime)
            routes_open = self._route_open(default_endtime=default_endtime)
        except NoDataError as err:
            if not (len(routes_closed) + len(routes_open)):
                raise

        return routes_closed, routes_open

    # _route ()

    def _auth(self, routes):
        """
        Perform endpoint authorization and map authorization objects to
        *restricted* routes.

        :param list routes: List of *restricted*
            :py:class:`eidangservices.utils.Route` objects.

        :returns: List of :py:class:`eidangservices.utils.Route` objects
            shipping authorization objects.
        :rtype: list
        """
        # TODO TODO TODO
        # Implement the authentication procedure described at #46
        # TODO TODO TODO
        #if self.token is not None:
        #    # get temporary credentials from endpoints; to be implemented
        #    # concurrently
        #    pass

        # map one and the same credentials to *closed* routes
        return [r._replace(auth=self.auth) for r in routes]

    # _auth ()

    @staticmethod
    def demux_routes(routes):
        return [utils.AuthRoute(url=route.url, streams=[se], auth=route.auth)
                for route in routes for se in route.streams]

# demux_routes ()


# class AuthMixin


# ---- END OF <auth.py> ----

# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# This is <process.py>
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
# 2018/03/29        V0.1    Daniel Armbruster
# =============================================================================
"""
federator processing facilities
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from builtins import * # noqa

import collections
import datetime
import logging
import multiprocessing as mp
import os
import time

from flask import current_app, stream_with_context, Response

from eidangservices import utils, settings
from eidangservices.federator import __version__
from eidangservices.federator.server.auth import AuthMixin
from eidangservices.federator.server.misc import (
    route as route_with_stationlite)
from eidangservices.federator.server.request import (
    RoutingRequestHandler, GranularFdsnRequestHandler)
from eidangservices.federator.server.task import (
    RawDownloadTask, RawSplitAndAlignTask, StationTextDownloadTask,
    StationXMLNetworkCombinerTask, WFCatalogSplitAndAlignTask)
from eidangservices.utils.error import ErrorWithTraceback
from eidangservices.utils.httperrors import FDSNHTTPError


# TODO(damb): This is a note regarding the federator-registered mode.
# Processors using exclusively DownloadTask objects must perform a detailed
# logging to the log DB. Processors using Combiners delegate logging to the
# corresponding combiner tasks.

def demux_routes(routes):
    return [utils.Route(url=route.url, streams=[se])
            for route in routes for se in route.streams]

# demux_routes ()

def group_routes_by(routes, key='network'):
    """
    Group routes by a certain :py:class:`eidangservices.sncl.Stream` property
    keyword. Combined keywords are also possible e.g. :code:`network.station`.
    When combining keys the seperating character is :code:`.`. Routes are
    returned demultiplexed.

    :param list routes: List of :py:class:`eidangservices.utils.Route` objects
    :param str key: Key used for grouping.
    """
    SEP = '.'

    routes = demux_routes(routes)
    retval = collections.defaultdict(list)

    for route in routes:
        try:
            _key = getattr(route.streams[0].stream, key)
        except AttributeError as err:
            try:
                if SEP in key:
                    # combined key
                    _key = SEP.join(getattr(route.streams[0].stream, k)
                                    for k in key.split(SEP))
                else:
                    raise KeyError(
                        'Invalid separator. Must be {!r}.'.format(SEP))
            except (AttributeError, KeyError) as err:
                raise RequestProcessorError(err)

        retval[_key].append(route)

    return retval

# group_routes_by ()

def flatten_routes(grouped_routes):
    return [route for routes in grouped_routes.values() for route in routes]


class RequestProcessorError(ErrorWithTraceback):
    """Base RequestProcessor error ({})."""

class StreamingError(RequestProcessorError):
    """Error while streaming ({})."""

# -----------------------------------------------------------------------------
class RequestProcessor(object):
    """
    Abstract base class for request processors.

    :param default_endtime: Datetime to be used for stream epochs not shipping
        any endtime
    :type default_endtime: :py:class:`datetime.datetime`
    """

    LOGGER = "flask.app.federator.request_processor"

    MIMETYPE = None
    POOL_SIZE = 5
    # MAX_TASKS_PER_CHILD = 4
    TIMEOUT_STREAMING = settings.EIDA_FEDERATOR_STREAMING_TIMEOUT

    def __init__(self, query_params={}, stream_epochs=[], post=True,
                 **kwargs):
        self.query_params = query_params
        self.stream_epochs = stream_epochs
        self.post = post

        self.mimetype = kwargs.get('mimetype', self.MIMETYPE)
        if not self.mimetype:
            raise TypeError('Invalid mimetype value: {!r}'.format(self.mimetype))

        self.content_type = (
            '{}; {}'.format(self.mimetype, settings.CHARSET_TEXT)
            if self.mimetype == settings.MIMETYPE_TEXT else self.mimetype)

        self._routing_service = current_app.config['ROUTING_SERVICE']

        self.logger = logging.getLogger(kwargs.get('logger', self.LOGGER))
        self._default_endtime = kwargs.get('default_endtime',
                                           datetime.datetime.utcnow())
        self._pool = None
        self._results = []
        self._sizes = []

        self._datetime_init = datetime.datetime.utcnow()

    # __init__ ()

    @staticmethod
    def create(service, *args, **kwargs):
        """
        Factory method for :py:class:`RequestProcessor` object instances.

        :param str service: Service identifier
        :param auth: Emerge a request processor specified by
            :code:`service` providing access to *restricted* data
        :type auth: None or a subcalss of :py:class:`requests.auth.AuthBase`
        :param kwargs: Additional keyword value parameters passed to the
            processor's constructor

        :return: A concrete :py:class:`RequestProcessor` implementation
        :rtype: :py:class:`RequestProcessor`
        :raises KeyError: if an invalid format string was passed
        """
        auth = kwargs.get('auth')

        if service == 'dataselect' and auth is None:
            return DataselectRequestProcessor(*args, **kwargs)
        elif service == 'dataselect' and auth:
            return AuthDataselectRequestProcessor(auth, *args, **kwargs)
        elif service == 'station' and auth is None:
            return StationRequestProcessor.create(
                kwargs['query_params'].get('format', 'xml'), *args, **kwargs)
        elif service == 'wfcatalog' and auth is None:
            return WFCatalogRequestProcessor(*args, **kwargs)
        else:
            raise KeyError('Invalid RequestProcessor chosen.')

    # create ()

    @property
    def DATETIME_INIT(self):
        return self._datetime_init

    @property
    def streamed_response(self):
        """
        Return a streamed :py:class:`flask.Response`.

        :rtype: :py:class:`flask.Response`
        """
        self._request()

        # XXX(damb): Only return a streamed response as soon as valid data
        # is available. Use a timeout and process errors here.
        self._wait()

        resp = Response(stream_with_context(self), mimetype=self.mimetype,
                        content_type=self.content_type)

        resp.call_on_close(self._call_on_close)

        return resp

    # streamed_response ()

    def _route(self, default_endtime):
        """
        Create the routing table using the routing service provided.

        :param default_endtime: Endtime to be used if stream epochs do not ship
            one
        :type default_endtime: :py:class:`datetime.datetime`
        """
        routing_request = RoutingRequestHandler(
            self._routing_service, self.query_params,
            self.stream_epochs)

        self.logger.info(
            "Fetching routes from {!r}".format(routing_request.url))

        return route_with_stationlite(
            (routing_request.post() if self.post else routing_request.get()),
            default_endtime=default_endtime,
            nodata=int(self.query_params.get(
                'nodata',
                settings.FDSN_DEFAULT_NO_CONTENT_ERROR_CODE)))

    # _route ()

    def _handle_error(self, err):
        self.logger.warning(str(err))

    def _handle_413(self, result):
        """
        Handle HTTP status code **413** from endpoints.

        :param result: Request which lead to code **413**
        :type result:
            :py:class:`eidangservices.federator.server.request.RequestHandlerBase`

        :raises: :py:class:`eidangservices.utils.httperrors.FDSNHTTPError`
        """
        self.logger.warning(
            'Handle endpoint HTTP status code 413 (url={}, '
            'stream_epochs={}).'.format(result.data.url,
                                        result.data.stream_epochs))
        raise FDSNHTTPError.create(413, service_version=__version__)

    # _handle_413 ()

    def _wait(self, timeout=None):
        """
        Wait for a valid endpoint response.

        :param int timeout: Timeout in seconds
        """
        if timeout is None:
            timeout = self.TIMEOUT_STREAMING

        result_with_data = False
        while True:
            ready = []
            for result in self._results:
                if result.ready():
                    _result = result.get()
                    if _result.status_code == 200:
                        result_with_data = True
                        break
                    elif _result.status_code == 413:
                        self._handle_413(_result)
                        ready.append(result)
                    else:
                        self._handle_error(_result)
                        self._sizes.append(0)
                        ready.append(result)

                # NOTE(damb): We have to handle responses > 5MB. Blocking the
                # processor by means of time.sleep makes executing
                # *DownloadTasks IO bound.
                time.sleep(0.01)

            for result in ready:
                self._results.remove(result)

            if result_with_data:
                break

            if (not self._results or datetime.datetime.utcnow() >
                self.DATETIME_INIT +
                    datetime.timedelta(seconds=timeout)):
                self.logger.warning(
                    'No valid results to be federated. ({})'.format(
                        ('No valid results.' if not self._results else
                         'Timeout ({}).'.format(timeout))))
                raise FDSNHTTPError.create(
                    int(self.query_params.get(
                        'nodata',
                        settings.FDSN_DEFAULT_NO_CONTENT_ERROR_CODE)))

    # _wait ()

    def _request(self):
        """
        Template method used to actually issue the endpoint requests. Must be
        implemented by concrete :py:class:`RequestProcessor` implementations.
        """
        raise NotImplementedError

    def _call_on_close(self):
        """
        Template method which will be called when :py:class:`flask.Response` is
        closed. By default pending tasks are terminated.

        When using `mod_wsgi <http://modwsgi.readthedocs.io/en/latest/>`_ the
        method is called either in case the request successfully was responded
        or an exception occurred while sending the response. `Graham Dumpleton
        <https://github.com/GrahamDumpleton>`_ descibes the situation in this
        `thread post
        <https://groups.google.com/forum/#!topic/modwsgi/jr2ayp0xesk>`_ very
        detailed.
        """
        self._pool.terminate()
        self._pool = None

    # _call_on_close ()

    def __iter__(self):
        """
        Template method making the processor *streamable*. Must be implemented
        by concrete :py:class:`RequestProcessor` implementations.
        """
        raise NotImplementedError

# class RequestProcessor


class DataselectRequestProcessor(RequestProcessor):
    """
    Federating request processor implementation for :code:`fdsnws-dataselect`.
    Controls both the federated downloading process and the merging of the
    `miniSEED http://ds.iris.edu/ds/nodes/dmc/data/formats/miniseed/` data,
    afterwards. Note, that this type of request processor does not handle
    access to *restricted* data.

    Handles HTTP status code **413** from endpoints by splitting a
    stream epoch into smaller chunks and merging those chunks appropriately.
    """

    LOGGER = "flask.app.federator.request_processor_raw"

    CHUNK_SIZE = 1024
    MIMETYPE = settings.DATASELECT_MIMETYPE

    @property
    def POOL_SIZE(self):
        return current_app.config['FED_THREAD_CONFIG']['fdsnws-dataselect']

    def _route(self, default_endtime):
        """
        Create the routing table using the routing service provided.

        :param default_endtime: Endtime to be used if stream epochs do not ship
            one
        :type default_endtime: :py:class:`datetime.datetime`
        """
        routing_request = RoutingRequestHandler(
            self._routing_service, self.query_params,
            self.stream_epochs, access='open')

        self.logger.info(
            "Fetching routes from {!r}".format(routing_request.url))

        return route_with_stationlite(
            (routing_request.post() if self.post else routing_request.get()),
            default_endtime=default_endtime,
            nodata=int(self.query_params.get(
                'nodata',
                settings.FDSN_DEFAULT_NO_CONTENT_ERROR_CODE)))

    # _route ()

    def _request(self):
        """
        Request data from :code:`fdsnws-dataselect` endpoints.
        """
        routes = demux_routes(
            self._route(default_endtime=self._default_endtime))

        pool_size = (len(routes) if
                     len(routes) < self.POOL_SIZE else self.POOL_SIZE)

        self.logger.debug('Init worker pool (size={}).'.format(pool_size))
        self._pool = mp.pool.ThreadPool(processes=pool_size)
        # NOTE(damb): With pleasure I'd like to define the parameter
        # maxtasksperchild=self.MAX_TASKS_PER_CHILD)
        # However, using this parameter seems to lead to processes unexpectedly
        # terminated. Hence some tasks never return a *ready* result.

        for route in routes:
            self.logger.debug(
                'Creating DownloadTask for {!r} ...'.format(
                    route))
            t = RawDownloadTask(
                GranularFdsnRequestHandler(
                    route.url,
                    route.streams[0],
                    query_params=self.query_params))
            result = self._pool.apply_async(t)
            self._results.append(result)

    # _request ()

    def _handle_413(self, result):
        self.logger.info(
            'Handle endpoint HTTP status code 413 (url={}, '
            'stream_epochs={}).'.format(result.data.url,
                                        result.data.stream_epochs))
        self.logger.debug(
            'Creating SAATask for (url={}, '
            'stream_epochs={}) ...'.format(result.data.url,
                                           result.data.stream_epochs))
        t = RawSplitAndAlignTask(
            result.data.url, result.data.stream_epochs[0],
            auth=result.data.auth,
            query_params=self.query_params,
            endtime=self._default_endtime)

        result = self._pool.apply_async(t)
        self._results.append(result)

    # _handle_413 ()

    def __iter__(self):
        """
        Process the federated request. Makes the processor *streamable*.
        """
        # TODO(damb): The processor has to write metadata to the log database.
        # Also in case of errors.

        def generate_chunks(fd, chunk_size=self.CHUNK_SIZE):
            while True:
                data = fd.read(chunk_size)
                if not data:
                    break
                yield data

        while True:

            ready = []
            for result in self._results:

                if result.ready():
                    _result = result.get()

                    if _result.status_code == 200:
                        self._sizes.append(_result.length)
                        self.logger.debug(
                            'Streaming from file {!r} (chunk_size={}).'.format(
                                _result.data, self.CHUNK_SIZE))
                        try:
                            with open(_result.data, 'rb') as fd:
                                for chunk in generate_chunks(fd):
                                    yield chunk
                        except Exception as err:
                            raise StreamingError(err)

                        self.logger.debug(
                            'Removing temporary file {!r} ...'.format(
                                _result.data))
                        try:
                            os.remove(_result.data)
                        except OSError as err:
                            RequestProcessorError(err)

                    elif _result.status_code == 413:
                        self._handle_413(_result)

                    else:
                        self._handle_error(_result)
                        self._sizes.append(0)

                    ready.append(result)

                # NOTE(damb): We have to handle responses > 5MB. Blocking the
                # processor by means of time.sleep makes executing
                # *DownloadTasks IO bound.
                time.sleep(0.01)

            # TODO(damb): Implement a timeout solution in case results are
            # never ready.
            for result in ready:
                self._results.remove(result)

            if not self._results:
                break

        self._pool.close()
        self._pool.join()
        self.logger.debug('Result sizes: {}.'.format(self._sizes))
        self.logger.info(
            'Results successfully processed (Total bytes: {}).'.format(
                sum(self._sizes)))

    # __iter__ ()

# class DataselectRequestProcessor


class StationRequestProcessor(RequestProcessor):
    """
    Base class for federating fdsnws.station request processor. While routing
    this processor interprets the `level` query parameter in order to reduce
    the number of endpoint requests.

    StationRequestProcessor implementations come along with a *reducing*
    :code:`_route ()` implementation. Routes received from the *StationLite*
    webservice are reduced depending on the value of the `level` query
    parameter.
    """

    LOGGER = "flask.app.federator.request_processor_station"

    def __init__(self, query_params={}, stream_epochs=[], post=True,
                 **kwargs):
        super().__init__(query_params, stream_epochs, post, **kwargs)

        self._level = query_params.get('level')
        if self._level is None:
            raise RequestProcessorError("Missing parameter: 'level'.")

    # __init__ ()

    @staticmethod
    def create(response_format, *args, **kwargs):
        if response_format == 'xml':
            return StationXMLRequestProcessor(*args, **kwargs)
        elif response_format == 'text':
            return StationTextRequestProcessor(*args, **kwargs)
        else:
            raise KeyError('Invalid RequestProcessor chosen.')

    # create ()

    def _route(self, default_endtime):
        return group_routes_by(
            super()._route(default_endtime=default_endtime), key='network')

    # _route ()

# class StationRequestProcessor


class StationXMLRequestProcessor(StationRequestProcessor):
    """
    This processor implementation implements fdsnws-station XML federatation
    using a two-level approach.

    This processor implementation implements federatation using a two-level
    approach.
    On the first level the processor maintains a worker pool (implemented by
    means of the python multiprocessing module). Special *CombiningTask* object
    instances are mapped to the pool managing the download for a certain
    network code.
    On a second level RawCombinerTask implementations demultiplex the routing
    information, again. Multiple DownloadTask object instances (implemented
    using multiprocessing.pool.ThreadPool) are executed requesting granular
    stream epoch information (i.e. one task per fully resolved stream
    epoch).
    Combining tasks collect the information from their child downloading
    threads. As soon the information for an entire network code is fetched the
    resulting data is combined and temporarly saved. Finally
    StationRequestProcessor implementations merge the final result.
    """
    MIMETYPE = settings.STATION_MIMETYPE_XML
    CHUNK_SIZE = 1024

    SOURCE = 'EIDA'
    HEADER = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<FDSNStationXML xmlns="http://www.fdsn.org/xml/station/1" '
              'schemaVersion="1.0">'
              '<Source>{}</Source>'
              '<Created>{}</Created>')
    FOOTER = '</FDSNStationXML>'

    def _request(self):
        """
        Request data from :code:`fdsnws-station` endpoints with
        :code:`format=xml`.
        """
        routes = self._route(default_endtime=self._default_endtime)

        pool_size = (len(routes) if
                     len(routes) < self.POOL_SIZE else self.POOL_SIZE)

        self.logger.debug('Init worker pool (size={}).'.format(pool_size))
        self._pool = mp.pool.Pool(processes=pool_size)
        # NOTE(damb): With pleasure I'd like to define the parameter
        # maxtasksperchild=self.MAX_TASKS_PER_CHILD)
        # However, using this parameter seems to lead to processes unexpectedly
        # terminated. Hence some tasks never return a *ready* result.

        for net, routes in routes.items():
            self.logger.debug(
                'Creating CombinerTask for {!r} ...'.format(net))
            t = StationXMLNetworkCombinerTask(
                routes, self.query_params, name=net)
            result = self._pool.apply_async(t)
            self._results.append(result)

        self._pool.close()

    # _request ()

    def __iter__(self):
        """
        Process the federated request. Makes the processor *streamable*.
        """
        def generate_chunks(fd, chunk_size=self.CHUNK_SIZE):
            while True:
                data = fd.read(chunk_size)
                if not data:
                    break
                yield data

        while True:
            ready = []
            for result in self._results:
                if result.ready():

                    _result = result.get()
                    if _result.status_code == 200:
                        if not sum(self._sizes):
                            yield self.HEADER.format(
                                self.SOURCE,
                                datetime.datetime.utcnow().isoformat())

                        self._sizes.append(_result.length)
                        self.logger.debug(
                            'Streaming from file {!r} (chunk_size={}).'.format(
                                _result.data, self.CHUNK_SIZE))
                        try:
                            with open(_result.data, 'r', encoding='utf-8') \
                                    as fd:
                                for chunk in generate_chunks(fd):
                                    yield chunk
                        except Exception as err:
                            raise StreamingError(err)

                        self.logger.debug(
                            'Removing temporary file {!r} ...'.format(
                                _result.data))
                        try:
                            os.remove(_result.data)
                        except OSError as err:
                            RequestProcessorError(err)

                    elif _result.status_code == 413:
                        self._handle_413(_result)

                    else:
                        self._handle_error(_result)
                        self._sizes.append(0)

                    ready.append(result)

            # TODO(damb): Implement a timeout solution in case results are
            # never ready.
            for result in ready:
                self._results.remove(result)

            if not self._results:
                break

        yield self.FOOTER

        self._pool.join()
        self.logger.debug('Result sizes: {}.'.format(self._sizes))
        self.logger.info(
            'Results successfully processed (Total bytes: {}).'.format(
                sum(self._sizes) + len(self.HEADER) - 4 + len(self.SOURCE) +
                len(datetime.datetime.utcnow().isoformat()) +
                len(self.FOOTER)))

    # __iter__ ()

# class StationXMLRequestProcessor


class StationTextRequestProcessor(StationRequestProcessor):
    """
    This processor implementation implements fdsnws-station text federatation.
    Data is fetched multithreaded from endpoints.
    """
    MIMETYPE = settings.STATION_MIMETYPE_TEXT
    HEADER_NETWORK = '#Network|Description|StartTime|EndTime|TotalStations'
    HEADER_STATION = (
        '#Network|Station|Latitude|Longitude|'
        'Elevation|SiteName|StartTime|EndTime')
    HEADER_CHANNEL = (
        '#Network|Station|Location|Channel|Latitude|'
        'Longitude|Elevation|Depth|Azimuth|Dip|SensorDescription|Scale|'
        'ScaleFreq|ScaleUnits|SampleRate|StartTime|EndTime')

    @property
    def POOL_SIZE(self):
        return current_app.config['FED_THREAD_CONFIG']['fdsnws-station-text']

    def _request(self):
        """
        Request data from :code:`fdsnws-station` endpoints with
        :code:`format=text`.
        """
        routes = flatten_routes(
            self._route(default_endtime=self._default_endtime))

        pool_size = (len(routes) if
                     len(routes) < self.POOL_SIZE else self.POOL_SIZE)

        self.logger.debug('Init worker pool (size={}).'.format(pool_size))
        self._pool = mp.pool.ThreadPool(processes=pool_size)

        for route in routes:
            self.logger.debug(
                'Creating DownloadTask for {!r} ...'.format(
                    route))
            t = StationTextDownloadTask(
                GranularFdsnRequestHandler(
                    route.url,
                    route.streams[0],
                    query_params=self.query_params))
            result = self._pool.apply_async(t)
            self._results.append(result)

        self._pool.close()

    # _request ()

    def __iter__(self):
        """
        Process the federated request. Makes the processor *streamable*.
        """
        while True:
            ready = []
            for result in self._results:
                if result.ready():

                    _result = result.get()
                    if _result.status_code == 200:
                        if not sum(self._sizes):
                            # add header
                            if self._level == 'network':
                                yield '{}\n'.format(self.HEADER_NETWORK)
                            elif self._level == 'station':
                                yield '{}\n'.format(self.HEADER_STATION)
                            elif self._level == 'channel':
                                yield '{}\n'.format(self.HEADER_CHANNEL)

                        self._sizes.append(_result.length)
                        self.logger.debug(
                            'Streaming from file {!r}.'.format(_result.data))
                        try:
                            with open(_result.data, 'r', encoding='utf-8') \
                                    as fd:
                                for line in fd:
                                    yield line
                        except Exception as err:
                            raise StreamingError(err)

                        self.logger.debug(
                            'Removing temporary file {!r} ...'.format(
                                _result.data))
                        try:
                            os.remove(_result.data)
                        except OSError as err:
                            RequestProcessorError(err)

                    elif _result.status_code == 413:
                        self._handle_413(_result)

                    else:
                        self._handle_error(_result)
                        self._sizes.append(0)

                    ready.append(result)

            # TODO(damb): Implement a timeout solution in case results are
            # never ready.
            for result in ready:
                self._results.remove(result)

            if not self._results:
                break

        self._pool.join()
        self.logger.debug('Result sizes: {}.'.format(self._sizes))
        self.logger.info(
            'Results successfully processed (Total bytes: {}).'.format(
                sum(self._sizes)))

    # __iter__ ()

# class StationTextRequestProcessor


class WFCatalogRequestProcessor(RequestProcessor):
    """
    Process a WFCatalog request.
    """
    LOGGER = "flask.app.federator.request_processor_wfcatalog"

    MIMETYPE = settings.WFCATALOG_MIMETYPE
    CHUNK_SIZE = 1024

    JSON_LIST_START = '['
    JSON_LIST_END = ']'
    JSON_LIST_SEP = ','

    @property
    def POOL_SIZE(self):
        return current_app.config['FED_THREAD_CONFIG']['eidaws-wfcatalog']

    def _request(self):
        """
        Request data from :code:`eidaws-wfcatalog` endpoints.
        """
        routes = demux_routes(
            self._route(default_endtime=self._default_endtime))

        pool_size = (len(routes) if
                     len(routes) < self.POOL_SIZE else self.POOL_SIZE)

        self.logger.debug('Init worker pool (size={}).'.format(pool_size))
        self._pool = mp.pool.ThreadPool(processes=pool_size)
        # NOTE(damb): With pleasure I'd like to define the parameter
        # maxtasksperchild=self.MAX_TASKS_PER_CHILD)
        # However, using this parameter seems to lead to processes unexpectedly
        # terminated. Hence some tasks never return a *ready* result.

        for route in routes:
            self.logger.debug(
                'Creating DownloadTask for {!r} ...'.format(
                    route))
            t = RawDownloadTask(
                GranularFdsnRequestHandler(
                    route.url,
                    route.streams[0],
                    query_params=self.query_params))
            result = self._pool.apply_async(t)
            self._results.append(result)

    # _request ()

    def _handle_413(self, result):
        self.logger.info(
            'Handle endpoint HTTP status code 413 (url={}, '
            'stream_epochs={}).'.format(result.data.url,
                                        result.data.stream_epochs))
        self.logger.debug(
            'Creating SAATask for (url={}, '
            'stream_epochs={}) ...'.format(result.data.url,
                                           result.data.stream_epochs))
        t = WFCatalogSplitAndAlignTask(
            result.data.url, result.data.stream_epochs[0],
            query_params=self.query_params,
            endtime=self._default_endtime)

        result = self._pool.apply_async(t)
        self._results.append(result)

    # _handle_413 ()

    def __iter__(self):
        """
        Process the federated request. Makes the processor *streamable*.
        """
        def generate_chunks(fd, chunk_size=self.CHUNK_SIZE):
            _size = os.fstat(fd.fileno()).st_size
            # skip leading bracket (from JSON list)
            fd.seek(1)
            while True:
                buf = fd.read(chunk_size)
                if not buf:
                    break

                if fd.tell() == _size:
                    # skip trailing bracket (from JSON list)
                    buf = buf[:-1]

                yield buf

        while True:
            ready = []
            for result in self._results:
                if result.ready():
                    _result = result.get()
                    if _result.status_code == 200:
                        if not sum(self._sizes):
                            # add header
                            yield self.JSON_LIST_START
                        else:
                            # prepend comma if not first stream epoch data
                            yield self.JSON_LIST_SEP

                        self.logger.debug(
                            'Streaming from file {!r} (chunk_size={}).'.format(
                                _result.data, self.CHUNK_SIZE))
                        try:
                            with open(_result.data, 'rb') as fd:
                                # skip leading bracket (from JSON list)
                                size = 0
                                for chunk in generate_chunks(fd,
                                                             self.CHUNK_SIZE):
                                    size += len(chunk)
                                    yield chunk

                            self._sizes.append(size)

                        except Exception as err:
                            raise StreamingError(err)

                        self.logger.debug(
                            'Removing temporary file {!r} ...'.format(
                                _result.data))
                        try:
                            os.remove(_result.data)
                        except OSError as err:
                            RequestProcessorError(err)

                    elif _result.status_code == 413:
                        self._handle_413(_result)

                    else:
                        self._handle_error(_result)
                        self._sizes.append(0)

                    ready.append(result)

                # NOTE(damb): We have to handle responses > 5MB. Blocking the
                # processor by means of time.sleep makes executing
                # *DownloadTasks IO bound.
                time.sleep(0.01)

            # TODO(damb): Implement a timeout solution in case results are
            # never ready.
            for result in ready:
                self._results.remove(result)

            if not self._results:
                break

        yield self.JSON_LIST_END

        self._pool.close()
        self._pool.join()
        self.logger.debug('Result sizes: {}.'.format(self._sizes))
        self.logger.info(
            'Results successfully processed (Total bytes: {}).'.format(
                sum(self._sizes) + 2 + len(self._sizes)-1))

    # __iter__ ()

# class WFCatalogRequestProcessor


class AuthDataselectRequestProcessor(AuthMixin, DataselectRequestProcessor):
    """
    Federating request processor implementation for :code:`fdsnws-dataselect`
    allowing access to *restricted* data, too.

    Controls authentication at endpoints including both the federated
    downloading process and the merging of the `miniSEED
    http://ds.iris.edu/ds/nodes/dmc/data/formats/miniseed/` data, afterwards.

    Handles HTTP status code **413** from endpoints by splitting a
    stream epoch into smaller chunks and merging those chunks appropriately.

    :param default_endtime: Endtime to be used if stream epochs do not ship
        one
    :type default_endtime: :py:class:`datetime.datetime`
    """

    LOGGER = "flask.app.federator.auth_request_processor_dataselect"

    CHUNK_SIZE = 1024

    def __init__(self, auth, query_params={}, stream_epochs=[], post=True, **kwargs):
        super().__init__(auth=auth, query_params=query_params,
                         stream_epochs=stream_epochs, post=post, **kwargs)

    # __init__ ()

    def _request(self):
        """
        Request both *open* and *restricted* data from
        :code:`fdsnws-dataselect` endpoints. Note, that firstly the *open* data
        is requested. The *restricted* data is requested, afterwards (After the
        authentication procedure).
        """
        routes_closed, routes_open = self._route(
            default_endtime=self._default_endtime)

        routes_open = demux_routes(routes_open)
        routes_closed = self.demux_routes(routes_closed)

        pool_size = min(max(len(routes_open), len(routes_closed)),
                        self.POOL_SIZE)

        self.logger.debug('Init worker pool (size={}).'.format(pool_size))
        self._pool = mp.pool.ThreadPool(processes=pool_size)
        # NOTE(damb): With pleasure I'd like to define the parameter
        # maxtasksperchild=self.MAX_TASKS_PER_CHILD)
        # However, using this parameter seems to lead to processes unexpectedly
        # terminated. Hence some tasks never return a *ready* result.

        # request *open* data
        for route in routes_open:
            self.logger.debug(
                'Creating DownloadTask for {!r} ...'.format(
                    route))
            t = RawDownloadTask(
                GranularFdsnRequestHandler(
                    route.url,
                    route.streams[0],
                    query_params=self.query_params))
            result = self._pool.apply_async(t)
            self._results.append(result)

        # proceed with authentication for restricted routes
        routes_closed = self._auth(routes_closed)

        # request *restricted* data
        for route in routes_closed:
            self.logger.debug(
                'Creating DownloadTask for {!r} ...'.format(
                    route))
            t = RawDownloadTask(
                GranularFdsnRequestHandler(
                    route.url,
                    route.streams[0],
                    auth=route.auth,
                    query_params=self.query_params))
            result = self._pool.apply_async(t)
            self._results.append(result)

    # _request ()

    def _handle_413(self, result):
        self.logger.info(
            'Handle endpoint HTTP status code 413 (url={}, '
            'stream_epochs={}).'.format(result.data.url,
                                        result.data.stream_epochs))
        self.logger.debug(
            'Creating SAATask for (url={}, '
            'stream_epochs={}) ...'.format(result.data.url,
                                           result.data.stream_epochs))

        try:
            auth = result.data.auth
        except AttributeError:
            auth = None

        t = RawSplitAndAlignTask(
            result.data.url, result.data.stream_epochs[0],
            auth=auth,
            query_params=self.query_params,
            endtime=self._default_endtime)

        result = self._pool.apply_async(t)
        self._results.append(result)

    # _handle_413 ()

# class AuthDataselectRequestProcessor


# ---- END OF <process.py> ----

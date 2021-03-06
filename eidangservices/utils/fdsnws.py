# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# This is <fdsnws.py>
# -----------------------------------------------------------------------------
#
# This file is part of EIDA NG webservices.
#
# EIDA NG webservices is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# EDIA NG webservices is distributed in the hope that it will be useful,
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
# 2018/06/05        V0.1    Daniel Armbruster
# =============================================================================
"""
General purpose utils for EIDA NG webservices.
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from builtins import * # noqa

import functools
import itertools
import sys
import traceback

import webargs

from webargs.flaskparser import FlaskParser
from webargs.flaskparser import parser as flaskparser

from eidangservices import settings
from eidangservices.utils.httperrors import FDSNHTTPError


# -----------------------------------------------------------------------------
class FDSNWSParserMixin(object):
    """
    Mixin providing additional FDSNWS specific parsing facilities for `webargs
    https://webargs.readthedocs.io/en/latest/`_ parsers.
    """

    @staticmethod
    def _parse_streamepochs_from_argdict(arg_dict):
        """
        Parse stream epoch (i.e. :code:`network`, :code:`net`, :code:`station`,
        :code:`sta`, :code:`location`, :code:`loc`, :code:`channel:,
        :code:`cha`, :code:`starttime`, :code:`start`, :code:`endtime` and
        :code:`end`)related parameters from a dictionary like structure.

        :param dict arg_dict: Dictionary like structure to be parsed

        :returns: Dictionary with parsed stream epochs
        :rtype: dict

        Keys automatically are merged. If necessary, parameters are
        demultiplexed.
        """
        def _get_values(keys, raw=False):
            """
            Look up :code:`keys` in :code:`arg_dict`.

            :param keys: an iterable with keys to look up
            :param bool raw: return the raw value if True - else the value is
                splitted i.e. a list is returned
            """
            for key in keys:
                val = arg_dict.get(key)
                if val:
                    if not raw:
                        return val.split(
                            settings.FDSNWS_QUERY_LIST_SEPARATOR_CHAR)
                    return val
            return None

        # _get_values ()

        # preprocess the req.args multidict regarding SNCL parameters
        networks = _get_values(('net', 'network')) or ['*']
        stations = _get_values(('sta', 'station')) or ['*']
        locations = _get_values(('loc', 'location')) or ['*']
        channels = _get_values(('cha', 'channel')) or ['*']

        stream_epochs = []
        for prod in itertools.product(networks, stations, locations, channels):
            stream_epochs.append({'net': prod[0],
                                  'sta': prod[1],
                                  'loc': prod[2],
                                  'cha': prod[3]})
        # add times
        starttime = _get_values(('start', 'starttime'), raw=True)
        if starttime:
            for stream_epoch_dict in stream_epochs:
                stream_epoch_dict['start'] = starttime
        endtime = _get_values(('end', 'endtime'), raw=True)
        if endtime:
            for stream_epoch_dict in stream_epochs:
                stream_epoch_dict['end'] = endtime

        return {'stream_epochs': stream_epochs}

    # _parse_streamepochs_from_argdict ()

    @staticmethod
    def _parse_postfile(postfile):
        """
        Parse a FDSNWS formatted POST request file.

        :param str postfile: Postfile content

        :returns: Dictionary with parsed parameters.
        :rtype: dict
        """
        retval = {'stream_epochs': []}

        for line in postfile.split('\n'):
            check_param = line.split(
                settings.FDSNWS_QUERY_VALUE_SEPARATOR_CHAR)
            if (len(check_param) == 2 and not
                    all(not v.strip() for v in check_param)):

                # add query params
                retval[check_param[0].strip()] = check_param[1].strip()
                # self.logger.debug('Query parameter: %s' % check_param)
            elif len(check_param) == 1:
                # parse StreamEpoch
                stream_epoch = line.split()
                if len(stream_epoch) == 6:
                    stream_epoch = {
                        'net': stream_epoch[0],
                        'sta': stream_epoch[1],
                        'loc': stream_epoch[2],
                        'cha': stream_epoch[3],
                        'start': stream_epoch[4],
                        'end': stream_epoch[5]}
                    retval['stream_epochs'].append(stream_epoch)
            else:
                # self.logger.warn("Ignoring illegal POST line: %s" % line)
                continue

        return retval

    # _parse_postfile ()

# class FDSNWSParserMixin


class FDSNWSFlaskParser(FDSNWSParserMixin, FlaskParser):
    """
    FDSNWS parser providing enhanced SNCL parsing facilities. The parser was
    implemented following the instructions from the `webargs documentation
    <https://webargs.readthedocs.io/en/latest/advanced.html#custom-parsers>`_.
    """

    def parse_querystring(self, req, name, field):
        """
        Parse SNCL arguments from :code:`req.args`.

        :param req: Request object to be parsed
        :type req: :py:class:`flask.Request`
        """
        return webargs.core.get_value(
            self._parse_streamepochs_from_argdict(req.args), name, field)

    # parse_querystring ()

    def parse_form(self, req, name, field):
        """
        Intended to emulate parsing SNCL arguments from FDSNWS formatted
        postfiles.

        :param req: Request object to be parsed
        :type req: :py:class:`flask.Request`

        See also:
        http://www.fdsn.org/webservices/FDSN-WS-Specifications-1.1.pdf
        """
        return webargs.core.get_value(
            self._parse_postfile(self._get_data(req)), name, field)

    # parse_form ()

    def _get_data(self, req, as_text=True,
                  max_content_length=settings.MAX_POST_CONTENT_LENGTH):
        """
        Savely reads the buffered incoming data from the client.

        :param req: Request the raw data is read from
        :type req: :py:class:`flask.Request`
        :param bool as_text: If set to :code:`True` the return value will be a
            decoded unicode string.
        :param int max_content_length: Max bytes accepted

        :returns: Byte string or rather unicode string, respectively. Depending
            on the :code:`as_text` parameter.
        """
        if req.content_length > max_content_length:
            err = webargs.WebargsError(
                "Request too large: {} bytes > {} bytes ".format(
                    req.content_length, max_content_length))

            if self.error_callback:
                self.error_callback(err, req)
            else:
                self.handle_error(err, req)

        return req.get_data(cache=True, as_text=as_text)

    # _get_data ()

# class FDSNWSFlaskParser


fdsnws_parser = FDSNWSFlaskParser()
use_fdsnws_args = fdsnws_parser.use_args
use_fdsnws_kwargs = fdsnws_parser.use_kwargs


# -----------------------------------------------------------------------------
def with_exception_handling(func, service_version):
    """
    Method decorator providing a generic exception handling. A well-formatted
    FDSN exception is raised. The exception itself is logged.
    """
    @functools.wraps(func)
    def decorator(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except FDSNHTTPError as err:
            raise err
        except Exception as err:
            # NOTE(damb): Prevents displaying the full stack trace. Just log
            # it.
            exc_type, exc_value, exc_traceback = sys.exc_info()
            self.logger.critical('Local Exception: %s' % type(err))
            self.logger.critical('Traceback information: ' +
                                 repr(traceback.format_exception(
                                     exc_type, exc_value, exc_traceback)))
            raise FDSNHTTPError.create(500, service_version=service_version)

    return decorator

# with_exception_handling ()


def with_fdsnws_exception_handling(service_version):
    """
    Wrapper of :py:func:`with_exception_handling`.
    """
    return functools.partial(with_exception_handling,
                             service_version=service_version)

# with_fdsnws_exception_handling ()


def register_parser_errorhandler(service_version):
    """
    Register webargs parser `errorhandler
    <https://webargs.readthedocs.io/en/latest/quickstart.html#error-handling>`_.
    """
    @fdsnws_parser.error_handler
    @flaskparser.error_handler
    def handle_parser_error(err, req):
        """
        configure webargs error handler
        """
        raise FDSNHTTPError.create(400, service_version=service_version,
                                   error_desc_long=str(err))

    return handle_parser_error

# register_parser_errorhandler ()


# ---- END OF <fdsnws.py> ----

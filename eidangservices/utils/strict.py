# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# This is <strict.py>
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
# Copyright (c) Sven Marti (ETH), Daniel Armbruster (ETH), Fabian Euchner (ETH)
#
# REVISION AND CHANGES
# 2018/06/05        V0.1    Sven Marti
# =============================================================================
"""
TODO
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from builtins import * # noqa

import functools
import inspect

from webargs.flaskparser import parser as flaskparser
from marshmallow import Schema

from eidangservices import settings
from eidangservices.utils.httperrors import FDSNHTTPError


# -----------------------------------------------------------------------------
class KeywordParser(object):
    """
    Abstract base class for keyword parsers.
    """
    __location_map = {
        'query': 'parse_querystring',
        'form': 'parse_form' }

    def parse_querystring(self):
        """
        Template method parsing from :code:`location=query`.
        """
        raise NotImplementedError

    # parse_querystring ()

    def parse_form(self):
        """
        Template method parsing from :code:`location=form`.
        """
        raise NotImplementedError

    # parse_form ()

    def get_default_request(self):

        raise NotImplementedError

    # get_default_request ()

    def parse(self, func, schemas, locations):
        """
        Validate request query parameters.

        :param schemas: Marshmallow Schemas to validate request after
        :type schemas: tuple/list of :py:class:`marshmallow.Schema`
            or :py:class:`marshmallow.Schema`
        :param locations:
        :type locations: tuple of str

        :raises: :py:class:`httperrors.BadRequestError` 
        """

        request = self.get_default_request()

        if isinstance(schemas, Schema):
            schemas = [schemas]

        parsers = []
        for l in locations:
            try:
                func = self.__location_map[l]
                if inspect.isfunction(func):
                    function = func
                else:
                    function = getattr(self, func)
                parsers.append(function)
            except KeyError:
                raise ValueError('Invalid location: {!r}'.format(l))

        @functools.wraps(func)
        def decorator(*args, **kwargs):

            req_args = set()
            valid_fields = set()

            for func in parsers:
                req_args.update(func(request))
                
            for s in schemas:
                valid_fields.update(s.fields.keys())

            invalid_args = req_args.difference(valid_fields)
            if invalid_args:
                err_message = 'Invalid request query parameters: {}'.format(
                        invalid_args)
                raise FDSNHTTPError.create(
                    400, error_desc_long=err_message)

        # decorator ()

        return decorator

    # parse ()

    def with_strict_args(self, schemas, locations=None):
        """
        Wrapper of :py:func:`parse`.
        """
        return functools.partial(self.parse,
                                 schemas=schemas,
                                 locations=locations)

    # with_strict_args ()

# class KeywordParser

# -----------------------------------------------------------------------------
class FDSNWSKeywordParser(KeywordParser):
    """
    FDSNWSKeywordParser.
    """

    def parse_querystring(self, req):
      
        return tuple(req.args.keys())

    # parse_querystring ()

    def parse_form(self, req):
        argmap = {}

        if isinstance(req.data, bytes):
            req.data = req.data.decode('utf-8')
        
        for line in req.data.split('\n'):
            _line = line.split(
                settings.FDSNWS_QUERY_VALUE_SEPARATOR_CHAR)
            if len(_line) != 2:
                continue

            if all(w == '' for w in _line):
                raise FDSNHTTPError.create(
                        400, error_desc_long='RTFM :).')

            argmap[_line[0]] = _line[1]

        return tuple(argmap.keys())

    # parse_form ()

    def get_default_request(self):

        return flaskparser.get_default_request()

    # get_default_request ()

# class FDSNWSKeywordParser

fdsnws_keywordparser = FDSNWSKeywordParser()
with_strict_args = fdsnws_keywordparser.with_strict_args


# ---- END OF <strict.py> ----

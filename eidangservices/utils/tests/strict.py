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
# EIDA NG webservices is distributed in the hope that it will be useful,
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
# 2017/11/20        V0.1    Daniel Armbruster
#
# =============================================================================
"""
EIDA NG webservices utility test facilities.
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from builtins import * # noqa

import datetime
import io
import unittest

import flask # noqa
import marshmallow as ma

from werkzeug.exceptions import HTTPException
from werkzeug.datastructures import MultiDict
from webargs.flaskparser import parser

from eidangservices.utils import fdsnws, schema, sncl, strict
from eidangservices.utils.httperrors import FDSNHTTPError

try:
    import mock
except ImportError:
    import unittest.mock as mock

# -----------------------------------------------------------------------------
class FDSNWSKeywordParserTestCase(unittest.TestCase):

    class TestSchema(ma.Schema):
        f = ma.fields.Str()

    # class TestSchema

    class TestError(Exception):
        pass

    # class TestError

    class TestReq(object):
        pass

    # class TestReq

    @mock.patch('flask.request')
    def test_parse_querystring(self, mock_request):
        mock_request.method = 'GET'
        mock_request.args = MultiDict({'f': 'val',
                                       'b': 'val'})

        reference_list = tuple(['f', 'b'])
        request_list = strict.fdsnws_keywordparser.parse_querystring(
                mock_request)

        self.assertTupleEqual(request_list, reference_list)

    # test_parse_querystring ()

    @mock.patch('flask.Request')
    def test_parse_valid_form(self, mock_request):
        mock_request.method = 'POST'
        mock_request.stream = io.StringIO(
            "f=val\nb=val")
        
        reference_list = tuple(['f', 'b'])
        request_list = strict.fdsnws_keywordparser.parse_form(mock_request)

        self.assertTupleEqual(request_list, reference_list)

    # test_parse_valid_form ()

    @mock.patch('flask.Request')
    def test_parse_invalid_form(self, mock_request):
        mock_request.method = 'POST'
        mock_request.stream = io.StringIO("=")

        with self.assertRaises(Exception):
            strict.fdsnws_keywordparser.parse_form(mock_request)

    # test_parse_invalid_form ()

    @mock.patch('flask.Request')
    def test_parse_empty_form(self, mock_request):
        mock_request.method = 'POST'
        mock_request.stream = io.StringIO("")

        reference_list = tuple()
        request_list = strict.fdsnws_keywordparser.parse_form(mock_request)
        
        self.assertTupleEqual(request_list, reference_list)

    # test_parse_empty_form ()

    @mock.patch('flask.Request')
    def test_parse_valid_form_with_sncl(self, mock_request):
        mock_request.method = 'POST'
        mock_request.data = \
            "f=val\nNL HGN ?? * 2013-10-10 2013-10-11"
        
        reference_list = tuple(['f'])

        request_list = strict.fdsnws_keywordparser.parse_form(mock_request)

        self.assertTupleEqual(request_list, reference_list)

    # test_parse_valid_form_with_sncl ()

    @mock.patch('flask.Request')
    def test_parse_empty_form_with_sncl(self, mock_request):
        mock_request.method = 'POST'
        mock_request.stream = io.StringIO(
            "NL HGN ?? * 2013-10-10 2013-10-11")
        
        reference_list = tuple()

        request_list = strict.fdsnws_keywordparser.parse_form(mock_request)

        self.assertTupleEqual(request_list, reference_list)

    # test_parse_empty_form_with_sncl ()

    @mock.patch('eidangservices.utils.strict.FDSNHTTPError')
    @mock.patch(
        'eidangservices.utils.strict.fdsnws_keywordparser.get_default_request'
    )
    def test_with_strict_args_get_invalid(self, mock_request_factory, mock_error):
        req = self.TestReq()
        req.method = 'GET'
        req.args = MultiDict({'f': 'val',
                              'b': 'val'})

        mock_request_factory.return_value = req
        mock_error.create.return_value = self.TestError()

        @strict.with_strict_args(
            self.TestSchema(),
            locations=('query',)
        )
        def viewfunc():
            pass

        with self.assertRaises(self.TestError):
            viewfunc()

    # test_with_strict_args_get_invalid ()

    @mock.patch('eidangservices.utils.strict.FDSNHTTPError')
    @mock.patch(
        'eidangservices.utils.strict.fdsnws_keywordparser.get_default_request'
    )
    def test_with_strict_args_get_valid(self, mock_request_factory, mock_error):
        req = self.TestReq()
        req.method = 'GET'
        req.args = MultiDict({'f': 'val',
                              'b': 'val'})

        mock_request_factory.return_value = req
        mock_error.create.return_value = self.TestError()

        @strict.with_strict_args(
            self.TestSchema(),
            locations=('query',)
        )
        def viewfunc():
            pass

        viewfunc()

    # test_with_strict_args_get_valid ()

    @mock.patch(
        'eidangservices.utils.strict.fdsnws_keywordparser.parse_form'
    )
    def test_with_strict_args_post_invalid(self, mock_parse_f):
        mock_parse_f.return_value = tuple(['f', 'b'])

        @strict.with_strict_args(
            self.TestSchema(),
            locations=('form',)
        )
        def viewfunc():
            pass

        with self.assertRaises(Exception):
            viewfunc()

    # test_with_strict_args_post_invalid ()

    @mock.patch(
        'eidangservices.utils.strict.fdsnws_keywordparser.parse_form'
    )
    def test_with_strict_args_post_valid(self, mock_parse_f):
        mock_parse_f.return_value = tuple(['f'])

        @strict.with_strict_args(
            self.TestSchema(),
            locations=('form',)
        )
        def viewfunc():
            pass

        viewfunc()

    # test_with_strict_args_post_valid ()

# class FDSNWSKeywordParserTestCase

# -----------------------------------------------------------------------------
if __name__ == '__main__': # noqa
    unittest.main()

# ---- END OF <strict.py> ----

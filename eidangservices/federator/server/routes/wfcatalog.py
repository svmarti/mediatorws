# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# This is <wfcatalog.py>
# -----------------------------------------------------------------------------
#
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
#
# REVISION AND CHANGES
# 2017/10/26        V0.1    Daniel Armbruster
# =============================================================================
"""
This file is part of the EIDA mediator/federator webservices.
"""
import datetime 
import logging

from flask import request
from webargs.flaskparser import use_args

from eidangservices import settings
from eidangservices.federator.server import \
        general_request, schema, httperrors, misc


class WFCatalogResource(general_request.GeneralResource):
    """
    Implementation of a `WFCatalog
    <https://www.orfeus-eu.org/data/eida/webservices/wfcatalog/>`_ resource.
    """

    LOGGER = 'flask.app.federator.wfcatalog_resource'

    def __init__(self):
        super(WFCatalogResource, self).__init__()
        self.logger = logging.getLogger(self.LOGGER)

    @use_args(schema.SNCLSchema(
        context={'request': request}), 
        locations=('query',)
    )
    @use_args(schema.WFCatalogSchema(), locations=('query',))
    def get(self, sncl_args, wfcatalog_args):
        """
        Process a *WFCatalog* GET request.
        """
        # request.method == 'GET'
        _context = {'request': request}
        # sanity check - starttime and endtime must be specified
        if not sncl_args or not all(len(sncl_args[t]) == 1 for t in
            ('starttime', 'endtime')):
            raise httperrors.BadRequestError(
                    settings.FDSN_SERVICE_DOCUMENTATION_URI, request.url,
                    datetime.datetime.utcnow()
            )

        args = {}
        # serialize objects
        s = schema.SNCLSchema(context=_context)
        args.update(s.dump(sncl_args).data)
        self.logger.debug('SNCLSchema (serialized): %s' % 
                s.dump(sncl_args).data)

        s = schema.WFCatalogSchema(context=_context)
        args.update(s.dump(wfcatalog_args).data)
        self.logger.debug('WFCatalogSchema (serialized): %s' % 
                s.dump(wfcatalog_args).data)

        # process request
        self.logger.debug('Request args: %s' % args)
        return self._process_request(args, settings.WFCATALOG_MIMETYPE,
            path_tempfile=self.path_tempfile)

    # get ()

    @misc.use_fdsnws_args(schema.SNCLSchema(
        context={'request': request}), 
        locations=('form',)
    )
    @misc.use_fdsnws_args(schema.WFCatalogSchema(), locations=('form',))
    def post(self, sncl_args, wfcatalog_args):
        """
        Process a *WFCatalog* POST request.
        """
        # request.method == 'POST'
        # NOTE: must be sent as binary to preserve line breaks
        # curl: --data-binary @postfile --header "Content-Type:text/plain"

        # serialize objects
        s = schema.SNCLSchema()
        sncl_args = s.dump(sncl_args).data
        self.logger.debug('SNCLSchema (serialized): %s' % sncl_args)

        s = schema.WFCatalogSchema()
        wfcatalog_args = s.dump(wfcatalog_args).data
        self.logger.debug('WFCatalogSchema (serialized): %s' % wfcatalog_args)
        self.logger.debug('Request args: %s' % wfcatalog_args)

        # merge SNCL parameters
        sncls = misc.convert_sncl_dict_to_lines(sncl_args)
        self.logger.debug('SNCLs: %s' % sncls)
        sncls = '\n'.join(sncls) 

        return self._process_request(wfcatalog_args, 
                settings.WFCATALOG_MIMETYPE, path_tempfile=self.path_tempfile,
                postdata=sncls)

    # post ()

# class WFCatalogResource

# ---- END OF <wfcatalog.py> ----

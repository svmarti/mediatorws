# -*- coding: utf-8 -*-
"""

EIDA Mediator

This file is part of the EIDA mediator/federator webservices.

"""

import copy
import os
import sys
import tempfile


import flask
from flask import make_response
from flask_restful import Resource

from intervaltree import Interval, IntervalTree

import obspy
import requests

from mediator import settings
from mediator.server import httperrors, parameters
from mediator.utils import misc


def process_dq(query_par, outfile):
    """
    Process direct query.
    
    query_par: DQRequestParser object
    
    """
    
    # event service: GET only
    # SC3 implementation: no catalogs parameter, contributors parameter are
    #  mapped to agencyIDs, they must be defined in
    #  @DATADIR@/share/fdsn/contributors.xml
    #  eventid (optional) is implemented, is a publicID
    
    # 15 events, NO PICKS
    # http://arclink.ethz.ch/fdsnws/event/1/query?
    # starttime=2016-01-01&endtime=2017-01-01&minlatitude=46&maxlatitude=49&minlongitude=8&maxlongitude=11&minmagnitude=3.5&format=xml&includearrivals=true&formatted=true
    
    # mediator query:
    # e.starttime=2016-01-01&e.endtime=2017-01-01&e.minlatitude=46&e.maxlatitude=49&e.minlongitude=8&e.maxlongitude=11&e.minmagnitude=3.5&e.format=xml&e.includearrivals=true&formatted=true
    
    # with SNCL constraint
    # s.network=CH&s.channel=HHZ
    
    # with SNCL/geometry constraint (no E geometry constraint)
    # TODO
    
    service = query_par.getpar('service')
    
    if query_par.service_enabled('event'):
        
        print "event query: %s" % query_par.event_params[1]
        
        # event query
        event_service = query_par.getpar('eventservice')
        event_query_url = misc.get_event_query_endpoint(event_service)
        print "querying %s" % event_query_url

        # consume event service
        try:
            r = requests.get(event_query_url, params=query_par.event_params[1])
        except Exception, e:
            raise RuntimeError, e
    
        cat_xml = str(r.text)
        #print cat_xml

        # only event w/o filtering requested: we are done
        # NOTE: don't catch output that is invalid QuakeML (e.g., HTML
        # error message returned by event service)
        if service == 'event' and not query_par.service_enabled('station'):
            if not cat_xml:
                raise httperrors.NoDataError()
            else:
                with open(outfile, 'wb') as fh:
                    print "writing raw event catalog"
                    fh.write(cat_xml)
                    
                return outfile
            
        try:
            cat = obspy.read_events(cat_xml)
        except Exception, e:
            err_msg = "catalog read failed: %s" % e
            raise RuntimeError, err_msg
    
        print len(cat)

        # browse through all waveform stream IDs in catalog
        try:
            # apply S SNCL constraint:
            # snclepochs: remove SNCLEs (OK)
            # catalog: remove whole events (TODO)
            snclepochs, cat = misc.get_sncl_epochs_from_catalog(cat, query_par)
        except Exception, e:
            err_msg = "SNCL epoch creation failed: %s" % e
            raise RuntimeError, err_msg
        
        print len(cat)
        print str(snclepochs)
        
        # TODO(fab): apply S geometry constraints (remove whole events)
        # requires to consume service S in order to get station coords

        # re-serialize filtered catalog w/ ObsPy
        # TODO(fab): ObsPy stops on illegal ResourceIdentifiers
        # possible fix: check every ResourceIdentifier (publicID) against
        # regular expression, if not valid, replace with random temp string,
        # save mapping fro temp to original ID, in final serialized document,
        # replace all temp IDs
        if service == 'event':
            if len(cat) == 0:
                raise httperrors.NoDataError()
            else:
                with open(outfile, 'wb') as fh:
                    print "writing filtered event catalog, %s events" % (
                        len(cat))
                    cat.write(outfile, format="QUAKEML")
                    
                return outfile
  
    else:
        # get sncl epochs w/o catalog
        start_time, end_time = parameters.get_start_end_time_par(
            query_par, service)
        
        # get SNCL constraints w/o catalog
        net, sta, loc, cha = parameters.get_sncl_par(query_par, service)

    
    if snclepochs.empty:
        raise httperrors.NoDataError()
    
    # TODO: check for wild cards in SNCLs
    # based on that list: consume dataselect/station

    # create POST data for federator service
    # TODO(fab): non-sncl parameter lines
    postdata = parameters.get_non_sncl_postlines(query_par)
    postdata += snclepochs.tofdsnpost()
    
    if not postdata:
        raise httperrors.NoDataError()
    
    federator_url = misc.get_federator_query_endpoint(
        misc.map_service(service))
    
    print federator_url
    print postdata
    
    #return None

    with open(outfile, 'wb') as fh:
        
        print "issueing POST to federator"
        
        response = requests.post(federator_url, data=postdata, stream=True)

        if not response.ok:
            error_msg = "federator POST failed with code %s" % (
                response.status_code)
            raise RuntimeError, error_msg

        for block in response.iter_content(1024):
            fh.write(block)
    
    return outfile

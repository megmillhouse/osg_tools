#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2015-2016 James Clark <james.clark@ligo.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import numpy as np
import time
import sys
import os, shutil
import socket
import subprocess
import uuid
import fileinput
import ast
import copy

from glue import pipeline

from lalapps import inspiralutils
from glue import segmentsUtils, segments

from optparse import OptionParser
import ConfigParser

import bwb_pipe_utils as pipe_utils

#############################################
#
# Local function defs
def confirm(prompt=None, resp=False):
    """Lifted from:
    http://code.activestate.com/recipes/541096-prompt-the-user-for-confirmation/
    Prompts for yes or no response from the user. Returns True for yes and
    False for no.

    'resp' should be set to the default value assumed by the caller when
    user simply types ENTER.

    >>> confirm(prompt='Proceed?', resp=True)
    Create Directory? [y]|n: 
    True
    >>> confirm(prompt='Proceed?', resp=False)
    Create Directory? [n]|y: 
    False
    >>> confirm(prompt='Proceed?', resp=False)
    Create Directory? [n]|y: y
    True

    """
    
    if prompt is None:
        prompt = 'Confirm'

    if resp:
        prompt = '%s [%s]|%s: ' % (prompt, 'y', 'n')
    else:
        prompt = '%s [%s]|%s: ' % (prompt, 'n', 'y')
        
    while True:
        ans = raw_input(prompt)
        if not ans:
            return resp
        if ans not in ['y', 'Y', 'n', 'N']:
            print 'please enter y or n.'
            continue
        if ans == 'y' or ans == 'Y':
            return True
        if ans == 'n' or ans == 'N':
            return False

def localize_xml(xmlfile, old_path, new_path):
    """
    Modify absolute paths in xml files to relative paths
    """

    f = open(xmlfile,'r')
    filedata = f.read()
    f.close()

    newdata = filedata.replace(old_path,new_path)

    shutil.move(xmlfile, xmlfile+'.bak')

    f = open(xmlfile,'w')
    f.write(newdata)
    f.close()

    return 0

def job_times(trigtime, seglen, psdlen, padding):
    """
    Compute the gps times corresponding to a given trigger time

    psdstart = trigtime - (psdlen + padding)
    start = floor(min(psdstart, trigtime-0.5*seglen))
    stop  = ceil(max(start+psdlen, trigtime+0.5*Sseglen))

    returns segment(start,stop), psdstart

    so that start can be used easily as a psd start
    """

    psdstart=trigtime - (0.5*psdlen + padding)
    start = np.floor(min(psdstart, trigtime-0.5*seglen))
    stop = np.ceil(max(start+psdlen, trigtime+0.5*seglen))

    return segments.segment(start,stop), psdstart

def dump_job_info(job_dir, trigger):
    """
    Writes a text file with job info to outputDir:

    GPS time, hl_lag or GraceID, frequency, and cWB’s rho
    """
    f=open(os.path.join(job_dir, 'job_info.txt'), 'w')

    f.write('# rho gps hl_lag hv_lag freq veto1 veto2 graceID\n')
    f.write('{rho} {gps_time} {hl_time_lag} {hv_time_lag} {trig_frequency} \
            {veto1} {veto2} {graceID}\n'.format(
        gps_time=trigger.trigger_time,
        hl_time_lag=trigger.hl_time_lag,
        hv_time_lag=trigger.hv_time_lag,
        trig_frequency=trigger.trigger_frequency,
        rho=trigger.rho,
        veto1=trigger.veto1,
        veto2=trigger.veto2,
        graceID=trigger.graceID))
    f.close()



def parser():
    """
    Parser for input (command line and ini file)
    """

    # --- cmd line
    parser = OptionParser()
    parser.add_option("-t", "--user-tag", default="", type=str)
    parser.add_option("-r", "--workdir", type=str, default=None)
    parser.add_option("--trigger-time", type=float, default=None)
    parser.add_option("--trigger-list", type=str, default=None)
    parser.add_option("--cwb-trigger-list", type=str, default=None)
    parser.add_option("--server", type=str, default=None)
    parser.add_option("--copy-frames", default=False, action="store_true")
    parser.add_option("--skip-datafind", default=False, action="store_true")
    parser.add_option("--sim-data", default=False, action="store_true")
    parser.add_option("-I", "--injfile", default=None)
    parser.add_option("-F", "--followup-injections", default=None)
    parser.add_option("-G", "--graceID", default=None)
    parser.add_option("--graceID-list", default=None)
    parser.add_option("--bw-inject", default=False, action="store_true")
    parser.add_option("--condor-submit", default=False, action="store_true")
    parser.add_option("--submit-to-gracedb", default=False, action="store_true")
    parser.add_option("--html-root", default=None)
    parser.add_option("--skip-megapy", default=False, action="store_true")
    parser.add_option("--skip-post", default=False, action="store_true")
    parser.add_option("--separate-post-dag", default=False, action="store_true")
    parser.add_option("--osg-jobs", default=False, action="store_true")
    parser.add_option("--abs-paths", default=False, action="store_true")
    parser.add_option("--fpeak-analysis", default=False, action="store_true")


    (opts,args) = parser.parse_args()

    if opts.workdir is None:
        print >> sys.stderr, "ERROR: must specify --workdir"
        sys.exit()


    if len(args)==0:
        print >> sys.stderr, "ERROR: require config file"
        sys.exit()
    if not os.path.isfile(args[0]):
        print >> sys.stderr, "ERROR: config file %s does not exist"%args[0]
        sys.exit()


    # --- Read config file
    cp = ConfigParser.ConfigParser()
    cp.optionxform = str
    cp.read(args[0])


    return opts, args, cp

# END --- Local function defs
#############################################

#############################################
# --- Parse options, arguments and ini file
opts, args, cp = parser()
cp.set('condor','copy-frames',str(opts.copy_frames))

workdir = opts.workdir 

if os.path.exists(workdir):
    # Prompt for confirmation to continue as this will overwrite existing
    # workflow files (but not resuilts)
    print >> sys.stderr, """
    \nXXX DANGER XXX: path {} already exists.

    Continuing workflow generation will OVERWRITE current workflow files
    (configuration file, injection data, DAGMAN and Bash scripts).  This may
    complicate book-keeping and is not recommended for production analyses.

    Proceeding is only recommended to re-run POSTPROCESSING.

    **Sanity is not guarenteed** if re-running parent bayeswave jobs\n""".format(
    workdir)

    if not confirm(prompt='Proceed?', resp=False):
        print >> sys.stderr, "You chose wisely, exiting"
        sys.exit()

else:
    print >> sys.stdout, "making work-directory: %s"%workdir
    os.makedirs(workdir)

# Decide whether OSG-submitting
if not cp.has_option('condor','osg-jobs'):
    cp.set('condor', 'osg-jobs', str(opts.osg_jobs))
elif cp.has_option('condor','osg-jobs') and opts.osg_jobs:
    # Override the config file with the command line
    cp.set('condor', 'osg-jobs', str(opts.osg_jobs))

# --- Decide whether analysing fpeak
if cp.has_option('bayeswave_fpeak_options', 'fpeak-analysis'):

    # override command line
    opts.fpeak_analysis=True

    if cp.has_option('bayeswave_fpeak_options','fpeak-srate'):
        fpeak_srate=cp.getfloat('bayeswave_fpeak_options','fpeak-srate')
    if cp.has_option('bayeswave_fpeak_options','fpeak-flow'):
        fpeak_flow=cp.getfloat('bayeswave_fpeak_options','fpeak-flow')

# --- Make local copies of necessary input files
shutil.copy(args[0], os.path.join(workdir, 'config.ini'))

# Injection file (e.g., sim-inspiral table).  Try commandline first, if none,
# try config file
injfile=opts.injfile
if injfile is None:
    try:
        injfile=cp.get('injections', 'injfile')
    except:
        injfile=None

if injfile is not None:
    # Copy injfile locally
    shutil.copy(injfile, workdir)
    injfile=os.path.basename(injfile)


# NR HDF5 data
nrdata=None
if injfile is not None and cp.has_option('injections','nrhdf5'):
    try:
        nrdata=cp.get('injections', 'nrhdf5')
        nr_full_path=cp.get('injections', 'nrhdf5')
    except:
        nrdata=None
    if not os.path.exists(nr_full_path): nrdata=None

    if nrdata is not None:
        shutil.copy(nrdata, workdir)
        nrdata=os.path.basename(nrdata)

        # Make sure normal permissions on hdf5
        os.chmod(os.path.join(workdir, nrdata), 0644)

        # Modify xml IN WORKDIR to point to local hdf5
        localize_xml(os.path.join(workdir, injfile), nr_full_path, nrdata)


# Skip segment queries?
print >> sys.stdout, "Determining whether to do segment queries"
try:
    skip_segment_queries = cp.getboolean('datafind','ignore-science-segments')
except ConfigParser.NoOptionError:
    print >> sys.stdout, \
            "No ignore-science-segments in [datafind], skipping segdb by default"
    cp.set('datafind','ignore-science-segments', str(True))
    skip_segment_queries=True

#############################################
#
# Get Trigger Info
#

# XXX: Careful, there's nothing here to handle the non-exclusivity of these
# options other than common sense
if opts.trigger_time is not None and not\
    cp.has_option('bayeswave_options','BW-inject'):
    #
    # Read trigger from commandline
    #
    trigger_list = pipe_utils.triggerList(cp, [opts.trigger_time])

if opts.trigger_list is not None:
    #
    # Read triggers from ascii list 
    #
    trigger_list = pipe_utils.triggerList(cp, trigger_file=opts.trigger_list)

if opts.cwb_trigger_list is not None:
    #
    # Read triggers from ascii list 
    #
    trigger_list = pipe_utils.triggerList(cp, cwb_trigger_file=opts.cwb_trigger_list)

if injfile is not None:
    #
    # Read injection file
    #
    injfilename=os.path.join(workdir,injfile)

    if opts.followup_injections is not None:
        # Create trigger list from union of injections and those in
        # followup_injections (overrides events= field)
        trigger_list = pipe_utils.triggerList(cp, injection_file=injfilename,
                followup_injections=opts.followup_injections)
    else:
        # Create trigger list from sim-inspiral table and events= field
        trigger_list = pipe_utils.triggerList(cp, injection_file=injfilename)

if cp.has_option('bayeswave_options','BW-inject'):
    # Check the option is valid:
    if cp.get('bayeswave_options','BW-inject') not in ['signal','glitch']:
        print >> sys.stderr, "Error: BW-inject must be in ", ['signal','glitch']
        sys.exit()
    #
    # Perform internal injections drawn from the signal or glitch model
    #
    if opts.trigger_time is None:
        opts.trigger_time=1126259462.392 
    print >> sys.stdout, "Setting trigger time to %f"%opts.trigger_time
    trigger_list = pipe_utils.triggerList(cp, gps_times=opts.trigger_time,
            internal_injections=True)
    
# GraceDB support
if opts.graceID is not None:

    graceIDs = [opts.graceID]
    trigger_list = pipe_utils.triggerList(cp, graceIDs=graceIDs)


if opts.graceID_list is not None:

    graceIDs = np.loadtxt(opts.graceID_list)
    trigger_list = pipe_utils.triggerList(cp, graceIDs=graceIDs)

if opts.submit_to_gracedb:
    if opts.html_root is None:
        html_root = cp.get('bayeswave_paths', 'html-root')
    else:
        html_root = opts.html_root
    if html_root is None:
        print >> sys.stder, "demanding submit to gdb but no html-root"
        sys.exit()



    if not os.path.exists(html_root):
        os.makedirs(html_root)
    else:
        print >> sys.stderr, "Warning: html-root %s exists"%html_root


# Extract trigger times for readability

trigger_times = [trig.trigger_time for trig in trigger_list.triggers]
hl_lag_times = [trig.hl_time_lag for trig in trigger_list.triggers]
hv_lag_times = [trig.hv_time_lag for trig in trigger_list.triggers]

#
# --- Determine min/max times for data coverage
#
psdlen = cp.getfloat('input','PSDlength')
padding = cp.getfloat('input','padding')
seglens = [trigger.seglen for trigger in trigger_list.triggers]

if cp.has_option('input','gps-start-time'):
    gps_start_time = cp.getint('input','gps-start-time')
else:
    trigtime = min(trigger_times) - (max(np.absolute(hl_lag_times))+25.0)
    seg, _ = job_times(trigtime, max(seglens), psdlen, padding)
    gps_start_time = seg[0]

if cp.has_option('input','gps-end-time'):
    gps_end_time = cp.getint('input','gps-end-time')
else:
    trigtime = max(trigger_times) + (max(np.absolute(hl_lag_times))+25.0)
    seg,_ = job_times(trigtime, max(seglens), psdlen, padding)
    gps_end_time = seg[1]

# Timelag adjustment
#gps_start_time = min(trigger_times) - (max(np.absolute(lag_times))+25.0)
#gps_end_time   = max(trigger_times) + (max(np.absolute(lag_times))+25.0)

# Update config parser
cp.set('input','gps-start-time',str(int(gps_start_time)))
cp.set('input','gps-end-time',str(int(gps_end_time)))

#############################################

# ----------------------------------------
# Setup analysis directory for deployment
# ----------------------------------------

topdir=os.getcwd()
os.chdir(workdir)

datafind_dir = 'datafind'
if not os.path.exists(datafind_dir): os.makedirs(datafind_dir)
if cp.has_option('injections', 'mdc-cache'):
    shutil.copy(cp.get('injections', 'mdc-cache'),
            os.path.join('datafind','MDC.cache'))


segment_dir = 'segments'
if not os.path.exists(segment_dir): os.makedirs(segment_dir)



################################################

# ----------------------------------------------
# Data Acquisition: gw_data_find & segdb queries
# ----------------------------------------------

#
# --- datafind params from config file
#
ifo_list=ast.literal_eval(cp.get('input','ifo-list'))
frtype_list=ast.literal_eval(cp.get('datafind', 'frtype-list'))

# Decide whether simulating data
if cp.has_option('datafind','sim-data'):
    cp.set('datafind','sim-data',str(True))

if not cp.has_option('datafind','sim-data'):
    cp.set('datafind', 'sim-data', str(opts.sim_data))
elif cp.has_option('datafind','sim-data') and opts.sim_data:
    # Override the config file with the command line
    cp.set('datafind', 'sim-data', str(opts.sim_data))



cache_files = {}
segmentList = {}
framePaths={}
frameSegs={}

#
# --- Handle special cases for segdb
#

if (opts.cwb_trigger_list is not None) \
        or (opts.trigger_list is not None) \
        or (opts.graceID is not None) \
        or (opts.graceID_list is not None):

    # Assume triggers lie in analyzeable segments
    skip_segment_queries=True

for ifo in ifo_list:

    if cp.getboolean('datafind','sim-data'):
        print >> sys.stdout, "Simulating noise"

        # Get the type of simulated data from the frame type list
        # E.g., to simulate from LALSimAdLIGO put this in the config.ini:
        #   frtype-list={'H1':'LALSimAdLIGO','L1':'LALSimAdLIGO'}
        # To simulate from arbitraray *A*SD:
        #   frtype-list={'H1':'interp:/home/tyson/O2/review/bayesline/IFO0_asd.dat','L1':'interp:/home/tyson/O2/review/bayesline/IFO0_asd.dat'}

        sim_spectrum = frtype_list[ifo]

        # If sim-data cache file is a reference PSD file, copy it to the work
        # directory
        if os.path.exists(sim_spectrum):
            print >> sys.stdout, \
                    "Attempting to copy ASD file to datafind directory"
            asd_path = os.path.join(datafind_dir,
                    os.path.basename(sim_spectrum))
            shutil.copy(sim_spectrum, asd_path)
            cache_files[ifo] = "interp:{0}".format(asd_path)

        else:
            cache_files[ifo] = frtype_list[ifo]

        segmentList[ifo] = \
                segments.segmentlist([segments.segment(gps_start_time,
                    gps_end_time)])

    else:


        #
        # --- Run DataFind query to produce cache files for frames
        #
        cachefilefmt = os.path.join(datafind_dir, '{0}.cache')
        cache_files[ifo]=os.path.join('datafind', '{0}.cache'.format(ifo))

        if opts.skip_datafind:
            print >> sys.stdout, \
                    "Copying cache files from [datafind], cache-files"
            manual_cache_files=ast.literal_eval(cp.get('datafind','cache-files'))
            shutil.copy(manual_cache_files[ifo], cache_files[ifo])
            
        else:

            if opts.server is not None:
                ldfcmd = "gw_data_find --observatory {o} --type {frtype} \
    -s {gps_start_time} -e {gps_end_time} --lal-cache\
    --server={server} -u {url_type} > {cachefile}".format(
                        o=ifo[0], frtype=frtype_list[ifo],
                        cachefile=cachefilefmt.format(ifo),
                        gps_start_time=gps_start_time,
                        gps_end_time=gps_end_time, server=opts.server,
                        url_type=cp.get('datafind','url-type'))
            else:
                ldfcmd = "gw_data_find --observatory {o} --type {frtype} -s \
    {gps_start_time} -e {gps_end_time} --lal-cache -u {url_type} >\
    {cachefile}".format( o=ifo[0], frtype=frtype_list[ifo],
    cachefile=cachefilefmt.format(ifo), gps_start_time=gps_start_time,
    gps_end_time=gps_end_time, url_type=cp.get('datafind','url-type'))
            print >> sys.stdout, "Calling LIGO data find ..."
            print >> sys.stdout, ldfcmd

            subprocess.call(ldfcmd, shell=True)

            ldfcmd_file = open('datafind_cmd.sh','w')
            ldfcmd_file.writelines(ldfcmd+'\n')
            ldfcmd_file.close()

        # Record frame segments so we can identify frames for OSG transfers
        if opts.skip_datafind:
            # XXX: if no datafind, assume frames include jobs.  But should
            # change to take cache file locations
            frameSegs[ifo] = \
                    segments.segmentlist([segments.segment(gps_start_time,
                        gps_end_time)])
        else:
            frameSegs[ifo] = segmentsUtils.fromlalcache(open(cache_files[ifo]))

        if skip_segment_queries:
            segmentList[ifo] = \
                    segments.segmentlist([segments.segment(gps_start_time,
                        gps_end_time)])
        else:

            #
            # --- Run segdb query
            #

            if cp.has_option('datafind','veto-categories'):
              veto_categories=ast.literal_eval(cp.get('datafind','veto-categories'))
            else: veto_categories=[]

            curdir=os.getcwd()
            os.chdir(segment_dir)

            (segFileName,dqVetoes)=inspiralutils.findSegmentsToAnalyze(cp, ifo,
                    veto_categories, generate_segments=True,
                    use_available_data=False, data_quality_vetoes=False)

            segfile=open(segFileName)
            segmentList[ifo]=segmentsUtils.fromsegwizard(segfile)
            segmentList[ifo].coalesce()
            segfile.close()

            if segmentList[ifo] == []:
                print >> sys.stderr, "No matching segments for %s"%ifo
                sys.exit()

            os.chdir(curdir)


        # --------------------------------------------------------------------
        # Set up cache files to point to local copies of frames in the working
        # directory

        if opts.copy_frames:
            print "Setting up frame copying"

            #
            # Now we need to make a new, local cache file
            # - do this by manipulating the path string in the cache file to be relative 
            cache_file = 'datafind/{ifo}.cache'.format(ifo=ifo)
            shutil.copy(cache_file, cache_file.replace('cache','cache.bk'))

            cache_entries = np.loadtxt(cache_file, dtype=str)
            if cache_entries.ndim==1: cache_entries = [cache_entries]
            
            framePaths[ifo]=[]
            new_cache = open(cache_file, 'w')
            for c,cache_entry in enumerate(cache_entries):
                frame = cache_entry[-1].split('localhost')[-1]
                framePaths[ifo].append(frame)

                #local_path=os.path.join('datafind',cache_entry[4].split('/')[-1])
                local_path=cache_entry[4].split('/')[-1]

                new_cache.writelines('{ifo} {type} {gps} {length} {path}\n'.format(
                    ifo=ifo, type=cache_entry[1], gps=cache_entry[2],
                    length=cache_entry[3], path=local_path))

            new_cache.close()

#########################################################################
# Setup paths if necessary
if opts.abs_paths: 
    # FIXME: needs to handle HDF5
    for ifo in ifo_list: cache_files[ifo] = os.path.abspath(cache_files[ifo])
    if injfile is not None: injfile=os.path.abspath(injfile)



#########################################################################
# -----------------------------------------------------------------------
# DAG Writing
# -----------------------------------------------------------------------

#
# Initialise DAG and Jobs
#

# ---- Create a dag to which we can add jobs.
dag = pipeline.CondorDAG(log=opts.workdir+'.log')
postdag = pipeline.CondorDAG(log=opts.workdir+'_post.log')
fpeakdag = pipeline.CondorDAG(log=opts.workdir+'_fpeak.log')

# ---- Set the name of the file that will contain the DAG.
dag.set_dag_file( 'bayeswave_{0}'.format(os.path.basename(opts.workdir)) )
postdag.set_dag_file( 'bayeswave_post_{0}'.format(os.path.basename(opts.workdir)) )
fpeakdag.set_dag_file( 'bayeswave_fpeak_{0}'.format(os.path.basename(opts.workdir)) )

# ---- Create DAG jobs
#   bayeswave: main bayeswave analysis
#   bayeswave_post: normal post-processing
#   bayeswave_fpeak: Spectral analysis post-processing (typically for BNS)
#   megasky: skymap job
#   megaplot: remaining plots & webpage generation
#   submitToGraceDB: upload skymap & PE to graceDB (optional)

bayeswave_job = pipe_utils.bayeswaveJob(cp, cache_files, injfile=injfile,
        nrdata=nrdata)

bayeswave_post_job = pipe_utils.bayeswave_postJob(cp, cache_files,
        injfile=injfile, nrdata=nrdata)

if opts.fpeak_analysis:
    # The fpeak job is simply an instance of the standard post-proc job with a
    # different executable 
    bayeswave_fpeak_job = pipe_utils.bayeswave_fpeakJob(cp, cache_files,
            injfile=injfile, nrdata=nrdata)

megasky_job = pipe_utils.megaskyJob(cp)
megaplot_job = pipe_utils.megaplotJob(cp)

if opts.submit_to_gracedb: submitToGraceDB_job = pipe_utils.submitToGraceDB(cp)

#
# Build Nodes
#
try:
    dataseed=cp.getint('input', 'dataseed')
except ConfigParser.NoOptionError:
    print >> sys.stderr, "[input] section requires dataseed for sim data"
    print >> sys.stderr, " (you need this in bayeswave_post, even if real data"
    print >> sys.stderr, "...removing %s"%workdir
    os.chdir(topdir)
    shutil.rmtree(workdir)
    sys.exit()

unanalyzeable_jobs = []

transferFrames={}
totaltrigs=0


for t,trigger in enumerate(trigger_list.triggers):

    print >> sys.stdout, "---------------------------------------"

    # -------------------------------------------
    # Check job times fall within available data
    job_segment, psd_start = job_times(trigger.trigger_time, trigger.seglen,
            psdlen, padding)

    for ifo in ifo_list:

        job_in_segments = [seg.__contains__(job_segment) \
                for seg in segmentList[ifo]]

        if not any(job_in_segments):

            bad_job={}
            bad_job['ifo']=ifo
            bad_job['trigger_time']=trigger.trigger_time
            bad_job['seglen']=trigger.seglen
            bad_job['psdlen']=psdlen
            bad_job['padding']=padding
            bad_job['job_segment']=job_segment
            bad_job['data_segments']=segmentList[ifo]

            unanalyzeable_jobs.append(bad_job)
            
            print >> sys.stderr, "Warning: No matching %s segments for job %d of %d"%(
                    ifo, t+1, len(trigger_times))
            print >> sys.stderr, bad_job
            break

    else:

        print >> sys.stdout, """Adding node for GPS {0} ({1} of {2})
    L1-timeslide {3}, V-timeslide {4} """.format(
                trigger.trigger_time, totaltrigs+1, len(trigger_times),
                    trigger.hl_time_lag, trigger.hv_time_lag)


        if not cp.getboolean('datafind','sim-data'):
            #
            # Identify frames associated with this job
            if opts.copy_frames:
                for ifo in ifo_list:
                    frame_idx = [seg.intersects(job_segment) for seg in frameSegs[ifo]]
                    transferFrames[ifo] = [frame for f,frame in
                            enumerate(framePaths[ifo]) if frame_idx[f]] 

        # Make output directory for this trigger
        outputDir  = 'bayeswave_' + str('%.9f'%trigger.trigger_time) + '_' + \
                str(float(trigger.hl_time_lag)) + '_' +\
                str(float(trigger.hv_time_lag)) #+ str(uuid.uuid4())

        if not os.path.exists(outputDir): os.makedirs(outputDir)

        dump_job_info(outputDir, trigger) 

        bayeswave_node = pipe_utils.bayeswaveNode(bayeswave_job)
        bayeswave_post_node = pipe_utils.bayeswave_postNode(bayeswave_post_job)

        if opts.fpeak_analysis:
            bayeswave_fpeak_node = \
                    pipe_utils.bayeswave_fpeakNode(bayeswave_post_job,
                            bayeswave_fpeak_job)

        megasky_node = pipe_utils.megaskyNode(megasky_job, outputDir)
        megaplot_node = pipe_utils.megaplotNode(megaplot_job, outputDir)

        if opts.submit_to_gracedb:
            htmlDir=os.path.join(html_root, outputDir)
            if not os.path.exists(htmlDir):
                os.makedirs(htmlDir)
            gracedb_node = pipe_utils.submitToGraceDBNode(submitToGraceDB_job,
                    outputDir, htmlDir)

        #
        # --- Add options for bayeswave node
        #
        bayeswave_node.set_trigtime(trigger.trigger_time)
        bayeswave_node.set_segment_start(trigger.trigger_time -
                trigger.seglen/2.)
        bayeswave_node.set_srate(trigger.srate)
        bayeswave_node.set_seglen(trigger.seglen)
        bayeswave_node.set_window(trigger.window)
        bayeswave_node.set_flow(ifo_list,trigger.flow)
        if cp.has_option('input','PSDstart'):
            psd_start=cp.getfloat('input','PSDstart')
        bayeswave_node.set_PSDstart(psd_start)
        if cp.has_option('input','rolloff'):
            bayeswave_node.set_rolloff(cp.getfloat('input','rolloff'))
        if opts.abs_paths: outputDir=os.path.abspath(outputDir)
        bayeswave_node.set_outputDir(outputDir)
        if transferFrames: bayeswave_node.add_frame_transfer(transferFrames)

        if cp.get('datafind','sim-data'):
            bayeswave_node.set_dataseed(dataseed)

        if cp.has_option('bayeswave_options','BW-inject'):
            bayeswave_node.set_BW_event(trigger.BW_event)

        #
        # --- Add options for bayeswave_post node
        #
        bayeswave_post_node.set_dataseed(dataseed)
        bayeswave_post_node.set_trigtime(trigger.trigger_time)
        bayeswave_post_node.set_segment_start(trigger.trigger_time -
                trigger.seglen/2.)
        bayeswave_post_node.set_srate(trigger.srate)
        bayeswave_post_node.set_seglen(trigger.seglen)
        bayeswave_post_node.set_window(trigger.window)
        bayeswave_post_node.set_flow(ifo_list,trigger.flow)
        if cp.has_option('input','PSDstart'):
            psd_start=cp.getfloat('input','PSDstart')
        bayeswave_post_node.set_PSDstart(psd_start)
        if cp.has_option('input','rolloff'):
            bayeswave_post_node.set_rolloff(cp.getfloat('input','rolloff'))
        bayeswave_post_node.set_outputDir(ifo_list, outputDir)

        if injfile is not None:
            bayeswave_node.set_injevent(trigger.injevent)
            bayeswave_post_node.set_injevent(trigger.injevent)

        if 'L1' in ifo_list:
            bayeswave_node.set_L1_timeslide(trigger.hl_time_lag)
            bayeswave_post_node.set_L1_timeslide(trigger.hl_time_lag)
        if 'V1' in ifo_list:    
            bayeswave_node.set_V1_timeslide(trigger.hv_time_lag)
            bayeswave_post_node.set_V1_timeslide(trigger.hv_time_lag)

        if cp.has_option('bayeswave_options','BW-inject'):
            bayeswave_post_node.set_BW_event(trigger.BW_event)

        #
        # --- Add options for bayeswave_fpeak node
        #
        if opts.fpeak_analysis:

            bayeswave_fpeak_node.set_dataseed(dataseed)
            bayeswave_fpeak_node.set_trigtime(trigger.trigger_time)
            bayeswave_fpeak_node.set_segment_start(trigger.trigger_time -
                    trigger.seglen/2.)
            bayeswave_fpeak_node.set_srate(fpeak_srate)
            bayeswave_fpeak_node.set_seglen(trigger.seglen)
            bayeswave_fpeak_node.set_window(trigger.window)
            bayeswave_fpeak_node.set_flow(ifo_list,fpeak_flow)
            if cp.has_option('input','PSDstart'):
                psd_start=cp.getfloat('input','PSDstart')
            bayeswave_fpeak_node.set_PSDstart(psd_start)
            if cp.has_option('input','rolloff'):
                bayeswave_fpeak_node.set_rolloff(cp.getfloat('input','rolloff'))
            bayeswave_fpeak_node.set_outputDir(ifo_list, outputDir)

            if injfile is not None:
                bayeswave_fpeak_node.set_injevent(trigger.injevent)

            if 'L1' in ifo_list:
                bayeswave_fpeak_node.set_L1_timeslide(trigger.hl_time_lag)
            if 'V1' in ifo_list:    
                bayeswave_fpeak_node.set_V1_timeslide(trigger.hv_time_lag)

            if cp.has_option('bayeswave_options','BW-inject'):
                bayeswave_fpeak_node.set_BW_event(trigger.BW_event)
                

        #
        # --- Add options for mega-scripts
        #
        megasky_node.set_outputDir(outputDir)
        megaplot_node.set_outputDir(outputDir)


        #
        # --- Add parent/child relationships
        #
        if not opts.skip_post and not opts.separate_post_dag:
            bayeswave_post_node.add_parent(bayeswave_node)
            if opts.fpeak_analysis:
                bayeswave_fpeak_node.add_parent(bayeswave_node)
        if not opts.skip_megapy:
            megasky_node.add_parent(bayeswave_post_node)
            megaplot_node.add_parent(bayeswave_post_node) 
        if opts.submit_to_gracedb:
            gracedb_node.add_parent(megaplot_node) 
            gracedb_node.add_parent(megasky_node) 

        # Add Nodes to DAG
        dag.add_node(bayeswave_node)
        if not opts.skip_post and not opts.separate_post_dag:
            dag.add_node(bayeswave_post_node)
            if opts.fpeak_analysis:
                dag.add_node(bayeswave_fpeak_node)
        elif not opts.skip_post and opts.separate_post_dag:
            postdag.add_node(bayeswave_post_node)
            if opts.fpeak_analysis:
                fpeakdag.add_node(bayeswave_fpeak_node)
        else:
            continue

        if not opts.skip_megapy and not opts.separate_post_dag:
            dag.add_node(megasky_node)
            dag.add_node(megaplot_node)
        elif not opts.skip_megapy and opts.separate_post_dag:
            postdag.add_node(megasky_node)
            postdag.add_node(megaplot_node)
            if opts.fpeak_analysis:
                fpeakdag.add_node(megasky_node)
                fpeakdag.add_node(megaplot_node)

        if opts.submit_to_gracedb:
            dag.add_node(gracedb_node)


        # --- Add

        dataseed+=1
        totaltrigs+=1


#
# Finalise DAG
#
# ---- Write out the submit files needed by condor.
dag.write_sub_files()
if opts.separate_post_dag:
    postdag.write_sub_files()
    if opts.fpeak_analysis:
        fpeakdag.write_sub_files()

# ---- Write out the DAG itself.
dag.write_dag()
dag.write_script()
if opts.separate_post_dag:
    postdag.write_dag()
    postdag.write_script()
    if opts.fpeak_analysis:
        fpeakdag.write_dag()
        fpeakdag.write_script()

# move back
os.chdir(topdir)

# print some summary info:
if len(trigger_times)-len(unanalyzeable_jobs)>0:
    print """
    Total number of requested trigger times: {ntrigs_desired}
    Number of triggers successfully added to DAG: {ntrigs_added}
    Number of triggers failing data criteria: {ntrigs_failed}

    To submit:
        cd {workdir}
        condor_submit_dag {dagfile}
    """.format(ntrigs_desired=len(trigger_times),
            ntrigs_added=len(trigger_times)-len(unanalyzeable_jobs),
            ntrigs_failed=len(unanalyzeable_jobs),
            workdir=workdir, dagfile=dag.get_dag_file())
else:
    print ""
    print "No analyzeable jobs in requested time"


if opts.condor_submit:
    # Auto-submit dag by cd-ing into the work-directory and submitting
    # chdir is useful with the OSG-friendly relative paths

    print "Submitting DAG..."
     
    os.chdir(workdir)
    x = subprocess.Popen(['condor_submit_dag',dag.get_dag_file()])
    x.wait()
    if x.returncode==0:
        print 'Submitted DAG file: ',dag.get_dag_file()
    else:
        print 'Unable to submit DAG file'
    os.chdir(topdir)





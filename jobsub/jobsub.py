#!/usr/bin/env python
"""
jobsub: a tool for EUTelescope job submission to Marlin 

Steering files are generated based on steering templates by substituting
job and run specific variables. The variable information can be
provided to jobsub by command line argument, by a config file or by a
text file with run parameters (in comma-separated value/csv format).

Run 
python jobsub.py --help
to see the list of command line options.

"""
import sys
import logging

def parseIntegerString(nputstr=""):
    """
    return a set of selected values when a string in the form:
    1-4,6
    would return:
    1,2,3,4,6
    as expected...
    (from http://thoughtsbyclayg.blogspot.de/2008/10/parsing-list-of-numbers-in-python.html)

    """
    selection = set()
    # tokens are comma seperated values
    tokens = [substring.strip() for substring in nputstr.split(',')]
    for i in tokens:
        try:
            # typically tokens are plain old integers
            selection.add(int(i))
        except ValueError:
            # if not, then it might be a range
            token = [int(k.strip()) for k in i.split('-')]
            if len(token) > 1:
                token.sort()
                # we have items seperated by a dash
                # try to build a valid range
                first = token[0]
                last = token[len(token)-1]
                for value in range(first, last+1):
                    selection.add(value)
    return selection # end parseIntegerString

def ireplace(old, new, text):
    """ 
    case insensitive search and replace function searching through string and returning the filtered string
    (based on http://stackoverflow.com/a/4773614)

    """
    idx = 0
    occur = 0
    while idx < len(text):
        index_l = text.lower().find(old.lower(), idx)
        if index_l == -1:
            if occur == 0:
                raise EOFError("Could not find string "+old)
            return text
        text = text[:index_l] + new + text[index_l + len(old):]
        idx = index_l + len(new)
        occur = occur+1
    if occur == 0:
        raise EOFError("Could not find string "+old)
    return text


def loadparamsfromcsv(csvfilename, runs):
    """ Load and parse the csv file for the given set of runs and
    return nested dictionary: a collection of dictionaries, one for
    each csv row matching a run number.

    """
    import csv
    import os.path

    log = logging.getLogger('jobsub')
    parameters_csv = {} # store all information needed from the csv file
    if csvfilename is None: 
        return parameters_csv # if no file name given, return empty collection here
    if not os.path.isfile(csvfilename): # check if file exists
        log.error("Could not find the specified csv file '"+csvfilename+"'!")
        exit(1)
    try:
        csvfile = open(csvfilename, 'rb')
        try:
            log.debug("Opening csv file '"+csvfilename+"'.")
            dialect = csv.Sniffer().sniff(csvfile.read(1024)) # test csv file format details
            log.debug("Determined the CSV dialect as follows: delimiter=%s, doublequote=%s, escapechar=%s, lineterminator=%s, quotechar=%s , quoting=%s, skipinitialspace=%s",dialect.delimiter, dialect.doublequote, dialect.escapechar, list(ord(c) for c in dialect.lineterminator), dialect.quotechar, dialect.quoting, dialect.skipinitialspace)
            csvfile.seek(0) # back to beginning of file
            reader = csv.DictReader(csvfile, dialect=dialect) # now process CSV file contents here and load them into memory
            reader.next() # python < 2.6 requires an actual read access before filling 'DictReader.fieldnames'
            log.debug("CSV file contains the header info: %s", reader.fieldnames)
            reader.fieldnames = [field.lower() for field in reader.fieldnames] # convert to lower case keys to avoid confusion
            if not "runnumber" in reader.fieldnames: # verify that we have a column "runnumber"
                log.error("Could not find a column with header label 'RunNumber' in file '"+csvfilename+"'!")
                return 1
            if "" in reader.fieldnames:
                log.warning("Column without header label encountered in csv file '"+csvfilename+"'!")
            log.info("Successfully loaded csv file'"+csvfilename+"'.")
            # first: search through csv file to find corresponding runnumber entry line for every run
            csvfile.seek(0) # back to beginning of file..
            reader.next()   # .. and skip the header line
            rowcount = 1
            missingRuns = runs.copy() # list of runs to look for in csv file
            for row in reader: # loop over all rows once
                rowcount = rowcount + 1
                try:
                    for run in missingRuns: # check all runs if runnumber matches
                        if int(row["runnumber"]) == run:
                            log.debug("Found entry in csv file for run "+str(run)+" in row number "+ str(rowcount))
                            parameters_csv[run] = {}
                            parameters_csv[run].update(row)
                            missingRuns.remove(run)
                            break
                except ValueError: # int conversion error
                    log.warn("Could not interpret run number in row number "+str(rowcount)+" in file '"+csvfilename+"'.")
                    continue
                if len(missingRuns)==0:
                    log.debug("Finished search for runs in csv file before reaching end of file")
                    break
            log.debug("Searched over "+str(rowcount)+" rows in file '"+csvfilename+"'.")
            if not len(missingRuns)==0:
                log.error("Could not find an entry for the following run numbers in '"+csvfilename+"': "+', '.join(map(str, missingRuns)))
        finally:
            csvfile.close()
    except csv.Error, e:
        log.error("Problem loading the csv file '"+csvfilename+"'({0}): {1}".format(e.errno, e.strerror))
        return 1
    return parameters_csv

def checkSteer(sstring):
    """ Check string for any occurance of @.*@ and return boolean. """
    log = logging.getLogger('jobsub')
    import re
    hits = re.findall("@.*@", sstring)
    if hits:
        log.error ("Missing configuration parameters: "+', '.join(map(str, hits)))
        return False
    else:
        return True

def runMarlin(filenamebase):
    """ Runs Marlin and stores log of output """
    log = logging.getLogger('jobsub.marlin')

    # need some addtional libraries for process interaction
    from subprocess import Popen, PIPE
    from threading  import Thread # threading used for non-blocking process output parsing
    try:
        from Queue import Queue, Empty # python 2.x
    except ImportError:
        from queue import Queue, Empty  # python 3.x

    # parsing process output using threads
    # (approach from http://stackoverflow.com/a/4896288)
    def enqueue_output(out, queue):
        """ feed queue with readline output """
        for line in iter(out.readline, ''):
            queue.put(line)
        out.close()
    import shlex        
    ON_POSIX = 'posix' in sys.builtin_module_names
    cmd = "Marlin "+filenamebase+".xml"
    rcode = None # the return code that will be set by a later subprocess method
    try:
        # run process
        log.info ("Now starting Marlin process: "+cmd)
        p = Popen(shlex.split(cmd), stdout=PIPE, stderr=PIPE, bufsize=1, close_fds=ON_POSIX)
        # setup output queues and threads
        qout = Queue()
        tout = Thread(target=enqueue_output, args=(p.stdout, qout))
        qerr = Queue()
        terr = Thread(target=enqueue_output, args=(p.stderr, qerr))
        # threads die with the program
        tout.daemon = True
        terr.daemon = True 
        tout.start()
        terr.start()
        log_file = open(filenamebase+".log", "w")
        try:
            while p.poll() is None:
                # read line without blocking
                try:  
                    line = qout.get_nowait() # or q.get(timeout=.1)
                    log.info(line.strip())
                    log_file.write(line)
                except Empty:
                    pass
                
                try:  
                    line = qerr.get_nowait() # or q.get(timeout=.1)
                    log.error(line.strip())
                    log_file.write(line)                     
                except Empty:
                    pass
        finally:
            log_file.close()
        rcode = p.returncode # get the return code
    except OSError, e:
        log.critical("Problem with Marlin execution: Command '%s' resulted in error #%s, %s", cmd, e.errno, e.strerror)
        exit(1)
    return rcode

def zipLogs(path,filename):
    import zipfile
    import os.path
    log = logging.getLogger('jobsub')
    try:     # compression module might not be available, therefore try import here
        import zlib
        compression = zipfile.ZIP_DEFLATED
        log.debug("Creating *compressed* log archive")
    except ImportError: # no compression module available, use flat files
        compression = zipfile.ZIP_STORED
        log.debug("Creating flat log archive")
    zf = zipfile.ZipFile(os.path.join(path,filename)+".zip", mode='w') # create new zip file
    try:
        zf.write(os.path.join(path,filename)+".xml", compress_type=compression) # store in zip file
        zf.write(os.path.join(path,filename)+".log", compress_type=compression) # store in zip file
        os.remove(os.path.join(path,filename)+".xml") # delete file
        os.remove(os.path.join(path,filename)+".log") # delete file
    finally:
        log.debug("Closing log archive file")
        zf.close()

def main(argv=None):
    """  main routine of jobsub: a tool for EUTelescope job submission to Marlin """
    log = logging.getLogger('jobsub') # set up logging
    formatter = logging.Formatter('%(asctime)s %(name)s(%(levelname)s): %(message)s',"%H:%M:%S")
    handler_stream = logging.StreamHandler()
    handler_stream.setFormatter(formatter)
    log.addHandler(handler_stream)

    import os.path
    import ConfigParser
    try:
        import argparse
    except ImportError:
        log.debug("No locally installed argparse module found; trying the package provided with jobsub.")
        # argparse is not installed; use (old) version provided with jobsub
        # determine path to subdirectory
        import inspect
        cmd_subfolder = os.path.realpath(os.path.abspath(os.path.join(os.path.split(inspect.getfile( inspect.currentframe() ))[0],"pymodules/argparse")))
        if cmd_subfolder not in sys.path:
            sys.path.insert(0, cmd_subfolder)
        # try again loading the module
        try:
            import argparse
        except ImportError:
            # nothing we can do now
            log.critical("Could not load argparse module. For python versions prior to 2.7, please install it from http://code.google.com/p/argparse")
            return 1

    if argv is None:
        argv = sys.argv
        argv.pop(0) # progName

    # command line argument parsing
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--conf-file", "--config", help="Load config file with global and task specific variables", metavar="FILE")
    parser.add_argument("-csv", "--csv-file", help="Load run-specific variables from table (csv format)", metavar="FILE")
    parser.add_argument("--log-file", help="Save log to specified file", metavar="FILE")
    parser.add_argument("-l", "--log", help="Set specified log level", metavar="LEVEL")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Use to write steering files but to omit Marlin execution")
    parser.add_argument('--option', '-o', action='append', metavar="NAME=VALUE", help="Specify further options such as beamenergy=5.3; overrides config file options.")
    parser.add_argument("jobtask", help="Task of job (e.g. convert, hitmaker, align)")
    parser.add_argument("runs", help="Runs to analyze; can be a comma-separated list or a range, e.g. 1056-1060", nargs='*')
    args = parser.parse_args(argv)

    runs = set()
    for runnum in args.runs:
        try:
            
            runs.update(parseIntegerString(runnum))
        except ValueError:
            log.error("The list of runs contains non-integer and non-range values: '%s'", runnum)
            return 2

    # set the logging level
    numeric_level = getattr(logging, "INFO", None) # default: warnings only
    if args.log:
        # Convert log level to upper case to allow the user to specify --log=DEBUG or --log=debug
        numeric_level = getattr(logging, args.log.upper(), None)
        if not isinstance(numeric_level, int):
            log.error('Invalid log level: %s' % args.log)
            return 2

    handler_stream.setLevel(numeric_level)
    log.setLevel(numeric_level)
    log.info( "Setting log level to %i", numeric_level )
    log.debug( "Command line arguments used: %s ", args )

    # set up submission log file if requested on command line
    if args.log_file:
        handler_file = logging.FileHandler([args.log_file])
        handler_file.setFormatter(formatter)
        handler_file.setLevel(numeric_level)
        log.addHandler(handler_file) 

    # dictionary keeping our paramters
    # here you can set some minimal default config values that will (possibly) be overwritten by the config file
    parameters = {"templatepath":".", "templatefile":args.jobtask+"-tmp.xml","logpath":"."}

    # read in config file if specified on command line
    if args.conf_file:
        config = ConfigParser.SafeConfigParser()
        # local variables useful in the context of the config; access using %(NAME)s in config
        config.set("DEFAULT","HOME",str(os.environ.get('HOME')))
        config.set("DEFAULT","EUTelescopePath",str(os.environ.get('EUTELESCOPE')))
        if not config.read([args.conf_file]):
            log.error("Could not read config file '%s'!", args.conf_file)
            return 1
        # merge with defaults and create final set of configuration parameters
        if config.has_section(args.jobtask):
            parameters.update(dict(config.items(args.jobtask)))
        else:
            log.warning("Config file '%s' is missing a section [%s]!", args.conf_file, args.jobtask)
        log.info("Loaded config file %s", args.conf_file)
    else:
        log.warn("No config file specified")
    
    # Parse option part of the  argument here -> overwriting config options
    if args.option is None:
        log.debug("Nothing to parse: No additional config options specified through command line arguments. ")
    else:
        try:
            cmdoptions = dict(x.split('=', 1) for x in args.option) # now parse any options given through the -o cmd line switch
        except ValueError:
            log.error( "Command line error: cannot parse --option argument. Please use a '--option name=value' format. ")
            return 2
        for key in cmdoptions: # and overwrite our current config settings
            log.debug( "Parsing cmd line: Setting "+key+" to value "+cmdoptions[key]+", overwriting value of "+parameters.get(key))
            parameters[key] = cmdoptions[key]

    log.debug( "Our final config:")
    for key, value in parameters.items():
        log.debug ( "     "+key+" = "+value)

    steeringTmpFileName = os.path.join(parameters["templatepath"], parameters["templatefile"])
    if not os.path.isfile(steeringTmpFileName):
        log.critical("Steering file template '"+steeringTmpFileName+"' not found!")
        return 1

    log.debug( "Opening steering file template "+steeringTmpFileName)
    steeringStringBase = open(steeringTmpFileName, "r").read()

    #Query replace steering template with our parameter set
    log.debug ("Generating base steering file")
    for key in parameters.keys():
        # check if we actually find all parameters from the config in the steering file
        try:
            # need not to search for config variables only concerning submission control
            if (not key == "templatefile" and not key == "templatepath"):
                steeringStringBase = ireplace("@" + key + "@", parameters[key], steeringStringBase)
        except EOFError:
            if (not key == "eutelescopepath" and not key == "home" and not key == "logpath"): # do not warn about default content of config
                log.warn(" Parameter '" + key + "' was not found in template file "+parameters["templatefile"])

    # CSV table
    log.debug ("Loading csv file (if requested)")
    parameters_csv = loadparamsfromcsv(args.csv_file, runs) # store all information needed from the csv file

    # setup mechanism to deal with user pressing ctrl-c in a safe way while we execute marlin later
    import signal
    keepRunning = {'Sigint':'no'}
    def signal_handler(signal, frame):
        """ log if SIGINT detected, set variable to indicate status """
        log.critical ('You pressed Ctrl+C!')
        keepRunning['Sigint'] = 'seen'
    prevINTHandler = signal.signal(signal.SIGINT, signal_handler)

    log.info("Will now start processing the following runs: "+', '.join(map(str, runs)))
    # now loop over all runs
    for run in runs:
        if keepRunning['Sigint'] == 'seen':
            log.critical("Stopping to process remaining runs now")
            break  # if we received ctrl-c (SIGINT) we stop processing here

        runnr = str(run).zfill(6)
        log.info ("Now processing steering file for run number "+runnr)

        # make a copy of the preprocessed steering file content
        steeringString = steeringStringBase

        # if we have a csv file we can parse, we will lookup the runnumber and replace any
        # variables identified by the csv header by the run specific value
        try:
            for field in parameters_csv[run].keys():
                # check if we actually find all parameters from the csv file in the steering file - warn if not
                log.debug("Parsing steering file for csv field name '%s'", field)
                try:
                    # check that the field name is not empty and do not yet replace the runnumber
                    if not field == "" and not field == "runnumber":                    
                        steeringString = ireplace("@" + field + "@", parameters_csv[run][field], steeringString)
                except EOFError:
                    log.warn(" Parameter '" + field + "' from the csv file was not found in template file "+steeringTmpFileName)
        except KeyError:
            log.debug("No information from CSV found for this run")

        try:
            steeringString = ireplace("@RunNumber@", runnr, steeringString)
        except EOFError:
            log.error("No reference to run number ('@RunNumber@') found in template file "+steeringTmpFileName)
            return 1
                
        if not checkSteer(steeringString):
            return 1

        log.debug ("Now writing steering file for run number "+runnr)
        basefilename = args.jobtask+"-"+runnr
        steeringFile = open(basefilename+".xml", "w")
        try:
            steeringFile.write(steeringString)
        finally:
            steeringFile.close()

        # bail out if running a dry run
        if args.dry_run:
            log.info("Dry run: skipping Marlin execution. Steering file written to "+basefilename+'.xml')
            return 0

        rcode = runMarlin(basefilename) # start Marlin execution
        if rcode == 0:
            log.info("Marlin finished successfully")
        else:
            log.error("Marlin application returned with code "+str(rcode))
        zipLogs(parameters["logpath"],basefilename)
    # return to the prvious signal handler
    signal.signal(signal.SIGINT, prevINTHandler)
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
#
# Processes TES or FET trace from input file, fits for shape parameters
#
# Michael Kelsey <kelsey@tamu.edu>, Texas A&M University 2023
#
# 20231210  Adapted from Warren Perry's trace_fitter.ipynb notebook
# 20240110  Adding support for FET traces, RDF input instead of root_numpy
# 20240115  Add diagnostic output, improve TES vs. FET configurations

def usage():
    print("""
trace_fitter.py [-h] [-p] [-v] [-d <det>] [-e <evt>] [-s <type>] [-c <chan>] <file>
    
Reads in TES or FET traces from single-detector file, fits for shape parameters
(I0, Ipeak/E, rise and fall).
             
Argument: <file>    DMC ROOT file from single-detector simulation
Options: -d <det>   Detector type name (iZIP5, NF-C, etc.)
         -e <event> Event number from file to be processed
         -c <chan>  Index (0 to N-1) of channel to be processed
         -s <type>  Sensor type (TES or FET)
         -h         [Optional] Display this usage information
         -p         [Optional] Generate plots of fit results
         -v         [Optional] Verbose output (not yet implemented!)
                      
Requires: Numpy, Matplotlib, SciPy, ROOT""")

### CONFIGURATION ###

import traces_rdf
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
import os, sys

global CDMS_SUPERSIM
CDMS_SUPERSIM = os.environ['CDMS_SUPERSIM']

### DIAGNOSTIC OUTPUT ###

verbose=False                   # Global variable, value set in getargs()
def printVerbose(string):
    if verbose: print(string)

def setVerbose(vb=True):        # To set the global from outside
    global verbose
    verbose = vb


### PRIMARY FUNCTIONS ###

sensorType = None               # Global variable, to use in internal functions

def traceFit(file, detname="", sensor="TES", event=0, channel=0, doplot=False):
    """Get specified TES or FET trace (event and channel) from DMC file,
       fit for shape and make overlay plots if requested"""
    printVerbose(f"traceFit(file='{file}', detname='{detname}', event={event},"
                 f" channel={channel}, sensor={sensor}, doplot={doplot})")

    global sensorType          # Record specified sensor for low-level functions
    sensorType = sensor

    if sensor == "TES":
        traceFit_TES(file, detname, event, channel, doplot)
    elif sensor == "FET":
        traceFit_FET(file, detname, event, channel, doplot)
    else:
        print(f"Invalid sensor type '{sensor}' specified.")
        exit(2)
        
        
def traceFit_TES(file, detname="", event=0, channel=0, doplot=False):
    """Get specified TES trace (event and channel) from DMC file,
       fit for shape"""
    printVerbose("traceFit_TES()")

    bins, trace, I0, PhononE = traces_rdf.getTES(file, event, channel)
    IversusE = max(trace)/PhononE
    
    #### Obtain figures of merit measurements for trace and template ####
    results = trace_fitting(bins, trace, TESshape, guessTES)
    if doplot:
        trace_plots(detname,"TES",channel,bins,trace,TESshape(bins,*results))
    
    a, tR, tF, offset = results            # Unroll results for reporting
    print(f'# {titleName} shape parameters (to generate templates)')
    print(f'I0\t\t{I0:.4e} microampere')
    print(f'IversusE\t{max(trace)/PhononE:.4e} microampere/eV')
    print(f'riseTime\t{tR:.4e} us')
    print(f'fallTime\t{tF:.4e} us')
    print(f'Offset  \t{offset:.4e} us')
    
def traceFit_FET(file, detname="", event=0, channel=0, doplot=False):
    """Get specified FET trace (event and channel) from DMC file, fit for shape"""
    printVerbose("traceFit_FET")

    titleName = detname if detname else "Trace"
    
    bins, trace, ChargeQ = traces_rdf.getFET(file, event, channel)

    #### Obtain figures of merit measurements for trace and template ####
    results = trace_fitting(bins, trace, FETshape, guessFET, False)    # Don't apply parameter bounds
    if doplot:
        trace_plots(detname,"FET",channel,bins,trace,FETshape(bins,*results))

    a, invTd, invTr, offset = results      # Unroll results for reporting
    print(f'# {titleName} shape parameters (to generate templates)')
    print(f'decayRate   \t{invTd:.4e}/us => decayTime\t{1./invTd:.4e} us')
    print(f'recoveryRate\t{invTr:.4e}/us => recoveryTime\t{1./invTr:.4e} us')
    print(f'Offset      \t{offset:.4e} us')

            

### IDEALIZED PULSE SHAPES FOR FITTING ###

def TESshape(x, a, t_r, t_f, offset):
    """Shape of flipped TES trace above baseline, with simple
       rise and fall times"""
    return a*(np.exp(-(x-offset)/t_f)-np.exp(-(x-offset)/t_r))

def FETshape(x, a, invTd, invTr, offset):
    """Shape of normalized FET trace above baseline, with simple
       decay and recovery rates
       NOTE: Peak value is not 'a'; it is a*(invTd-invTr)"""
    return a*(np.exp(-(x-offset)*invTd)*invTd - np.exp(-(x-offset)*invTr)*invTr)


### FITTING BOUNDS AND INITIAL VALUE ESTIMATES ###

def guessTES(bins, trace):
    """Returns initial guesses for TES fit rise and fall time for curve_fit"""
    peak = trace.max()
    ipeak = trace.argmax()
    printVerbose(f"guessTES: peak {peak} @ bin {ipeak} (t {bins[ipeak]})")
    
    # Rise time: look for two e-foldings on rising side
    rlo = np.nonzero(trace[:ipeak]<=0.1*peak)[0][-1]    # End of rising edge
    rhi = np.nonzero(trace[:ipeak]<=0.2*peak*np.e)[0][-1]
    riseGuess = (bins[rhi]-bins[rlo])
    
    # Fall time: look for two e-foldings on falling side
    flo = np.nonzero(trace[ipeak:]<=0.8*peak)[0][0]     # Start of falling tail
    fhi = np.nonzero(trace[ipeak:]<=0.4*peak/np.e)[0][0]
    fallGuess = (bins[fhi]-bins[flo])/2

    # Analytic peak position is where d/dt of pulse shape is zero
    # ==> t_peak = tR * ln[(tF+tR)/tR]
    tpeak = riseGuess * np.log((fallGuess+riseGuess)/riseGuess)
    offsetGuess = bins[ipeak] - tpeak
    
    # Scale factor should be max of shape scaled by actual peak value
    pmax = pulseShape(tpeak, 1., riseGuess, fallGuess, 0.)
    scaleGuess = peak / pmax

    printVerbose(f"guessTES: scale {scaleGuess:.4e} rise {riseGuess:.4e},"
                 f" fall {fallGuess:.4e}, offset {offsetGuess:.4e} us")
    
    return scaleGuess, riseGuess, fallGuess, offsetGuess

def guessFET(bins, trace):
    """Returns initial guesses for FET fit inverse decay and recovery times"""
    
    peak = trace.max()
    ipeak = trace.argmax()
    printVerbose(f"guessFET: peak {peak} @ bin {ipeak} (t {bins[ipeak]})")

    # Peak should be at t=+binWidth (first bin after trigger)
    istart = np.nonzero(bins>=0.)[0][0]+1
    offsetGuess = ipeak - istart
    
    # Decay time: look for second e-folding after the peak
    dhi = np.nonzero(trace[ipeak:]>=peak/(2.*np.e))[0][-1]
    decayGuess = 2./(bins[dhi]-bins[0])
    
    # Recover time; look for second e-folding beyond minimum
    tmin = trace.min()
    imin = trace.argmin()
    printVerbose(f" bottom {tmin} @ bin {imin} (t {bins[imin]})")

    recoveryGuess = 0.
    if tmin < 0:
        tlast = trace[imin:].max()
        rlo = np.nonzero(trace[imin:]>=tmin*0.8)[0][0]
        rhi = np.nonzero(trace[imin:]>=min(tmin*0.4/np.e,tlast))[0][0]
        recoveryGuess = 2./(bins[rhi]-bins[rlo])
        if recoveryGuess < 0.1*decayGuess:
            printVerbose("f recoveryGuess {recoveryGuess} not physical.")
            recoveryGuess = 0.

    # FET function is [A/(D-R)]*(D*exp(-t*D) - R*exp(-t*R))
    scaleGuess = peak / (decayGuess-recoveryGuess)

    printVerbose(f"guessTES: scale {scaleGuess:.4e} decay {decayGuess:.4e} /us,"
                 f" recovery {recoveryGuess:.4e} /us, offset {offsetGuess:.4e} us")
    
    return scaleGuess, decayGuess, recoveryGuess, offsetGuess


def guessRange(guessFunc=None):
    """Compute allowed parameter ranges for fit based on initial guess values"""
    print(f"guessRange(guessFunc={guessFunc})")

    if guessFunc is None:
        return (-np.inf, np.inf)

    lower = 0.1*np.array(guess)
    upper = 5.*np.array(guess)
    bounds = (lower, upper)

    for i,g in enumerate(guess):       # Bounds can't both be zero!
        if guess[i] == 0.:
            bounds[0][i] = -np.inf
            bounds[1][i] = np.inf

    return bounds


def fittingRange(trace, cut=0.2):
    """Return starting and ending points for pulse fit, corresponding to
       'cut' height on either side of peak.  Assumes TES trace has been
       baseline-subtracted and flipped"""
    peak = max(trace)          # Peak Height
    ipeak = trace.tolist().index(peak)
    printVerbose(f"fittingRange: peak {peak} @ bin {ipeak}")

    ilo = 0
    ihi = len(trace)
    
    global sensorType
    if sensorType=="TES":
        ilo = np.nonzero(trace[:ipeak]<=cut*peak)[0][-1]          # End of rising edge
        ihi = ipeak+np.nonzero(trace[ipeak:]<=cut*peak)[0][0]     # Start of falling tail
        printVerbose(f"fittingRange: TES I>{cut}*peak [{ilo}:{ihi}]")
    elif sensorType=="FET":
        ilo = trace.argmax()+1
        ihi = ilo+2000          # Better to use initial guess of decay/recovery times
        printVerbose(f"fittingRange: FET [{ilo}:{ihi}]")

    return ilo, ihi


### General fitting function: sensor-specific info is in 'pulseShape' and 'guessFunc'

def trace_fitting(bins, trace, pulseShape, guessFunc=None, dobounds=True):
    """Fits input trace with binning to TES or FET shape; using function
       for initial values
       Output: a      = scale factor from fit
               t1     = rise time for TES, or decay time for FET
               t2     = fall time for TES, or recovery time for FET
               offset = t0 of best fit relative to t=0 bin
    """
    printVerbose(f"trace_fitting(bins, trace, pulseShape={pulseShape},"
                 f" guessFunc={guessFunc}, dobounds={dobounds})")
    
    start, end = fittingRange(trace)

    guess = guessFunc(bins, trace) if guessFunc else None
    bounds = guessRange(guessFunc) if dobounds else (-np.inf,np.inf)
    
    printVerbose(f" range [{start}:{end}]\n guess {guess}\n bounds{bounds}")
                
    params, _ = curve_fit(pulseShape, bins[start:end], trace[start:end],
                          p0=guess, bounds=bounds)
    printVerbose(f" final result {params}")

    a, t1, t2, offset = params    
    return a, t1, t2, offset


def trace_plots(detname, sensor, channel, bins, trace, fitshape):
    """Generate linear and log overlays of trace and fitted function"""
    printVerbose(f"tracePlots(detname='{detname}', bins, trace, fitshape)")
    
    titleName = detname if detname else "Trace"
    template = traces_rdf.getTemplate(detname, channel, sensor)
    
    trace_overlay(titleName, sensor, bins, trace, fitshape, template)
    plt.savefig(f"{titleName}-{sensor}_traceFit.eps", format='eps')
    plt.savefig(f"{titleName}-{sensor}_traceFit.png")

    
def trace_overlay(detname, sensor, bins, trace, fitshape, template):
    """Plots TES or FET trace (log and linear) with specified binning, overlaid
       with fitted shape and template detname argument used for plot title
    """
    printVerbose(f"trace_overlay(detname='{detname}', sensor='{sensor}',"
                 f" bins, trace, fitshape, template)")
    
    units  = { "TES": "\mu A",
               "FET": "mV" }
    xlim   = { "TES": [ [max(-100,bins.min()),min(3000,bins.max())],
                        [max(-10,bins.min()),min(300,bins.max())] ],
               "FET": [ [max(-100,bins.min()),min(1000,bins.max())],
                        [max(-10,bins.min()),min(300,bins.max())] ] }
    yscale = { "TES": ["log","linear"],
               "FET": ["linear","linear"] }

    start, end = fittingRange(trace)
    fig, axes = plt.subplots(1, 2, figsize=(12*0.7, 4*0.7), dpi=200)
    for plot in range(2):
        currentAxis = axes.flatten()[plot]

        if template is not None:
            currentAxis.plot(bins,template*max(trace),lw=1,ls='--',
                             color='black', label='Template')

        currentAxis.plot(bins, trace,lw=1,ls='-',color='red',label='Simulation')
        currentAxis.plot(bins[start:end], fitshape[start:end], label='Fit')
        
        currentAxis.set_xlabel(r"Time [$\mathrm{\mu s}$]")
        currentAxis.set_ylabel(r"Amplitude [$\mathrm{"+units[sensor]+"}$]")
        currentAxis.legend()
        currentAxis.set_xlim(xlim[sensor][plot])
        currentAxis.set_yscale(yscale[sensor][plot])

    if detname: plt.title(detname)
    plt.tight_layout()


### MAIN PROGRAM ###

def main():
    settings = getargs()
    traceFit(**settings)

def getargs():
    """Returns arguments from the command line as a dictionary, for easier use.
       Output: setttings = { 'file':    <name-of-DMC-file>,
                             'detname': <detname, from -d>,
                             'event':   <event number, from -e>,
                             'channel': <channel number, from -c>,
                             'sensor':  <sensor type, from -s>,
                             'doplot':  <True|False>, from -p> }
    """
    import getopt
    try:
        opts, args = getopt.getopt(sys.argv[1:], 'c:d:e:hpv')
    except getopt.GetoptError as err:
        sys.exit(2)

    if args is None or len(args)==0:
        usage()
        sys.exit(2)
        
    settings = {'file':    args[0],   # Filename is required
                'detname': "",        # Detector name not required
                'event':   0,         # First event, first channel
                'channel': 0,
                'sensor':  "TES",     # TES or FET
                'doplot':  False,     # Results only, no figures
               }

    for o,a in opts:
        if   o == '-c':
            settings['channel'] = int(a)
        elif o == '-d':
            settings['detname'] = a
        elif o == '-e':
            settings['event'] = int(a)
        elif o == '-h':
            usage()
            sys.exit(0)
        elif o == '-p':
            settings['doplot'] = True
        elif o == '-s':
            settings['sensor'] = a
        elif o == '-v':
            setVerbose(true)

    printVerbose(f"settings = {settings}")
        
    return settings


### COMMAND-LINE CALL ###

if __name__ == "__main__":
    main()
    
### END OF FILE ###
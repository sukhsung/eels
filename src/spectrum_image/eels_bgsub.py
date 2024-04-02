import numpy as np
import copy
from tqdm import tqdm, tqdm_notebook
import numpy.linalg as LA
from scipy.ndimage import gaussian_filter
from scipy.stats import norm
import spectrum_image.SI_lineshapes as ls
from scipy.optimize import curve_fit


######## Background Subtractions
def bgsub_SI( si, energy, edge,log=False, fit='pl', mask=None, threshold=None,
             lc=False, perc=(5,95), lba=False, gfwhm=None,
            maxfev=50000, method='trf', ftol=0.0005, gtol=0.00005, xtol=None):
    """
    Full background subtraction function-
    Optional LBA, log fitting, LCPL, and exponential fitting.
    For more information on non-linear fitting function, see information at https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.curve_fit.html

    Inputs:
    raw_data - SI
    energy_axis - corresponding energy axis
    edge - edge parameters defined by KEM convention

    **kawrgs:
    fit - choose the type of background fit, default == 'pl' == Power law. Can also use 'exp'== Exponential, 'lin' == Linear.
    gfwhm - If using LBA, gfwhm corresponds to width of gaussian filter, default = None, meaning no LBA.
    log - Boolean, if true, log transform data and fit using QR factorization, default == False.
    lc - Boolean, if true, include LCPL or LCEX background subtracted SI, default == False.
    perc - standard deviation spread of r values from power law fitting. Default == (5/95)
    ftol - default to 0.0005, Relative error desired in the sum of squares.
    gtol - default to 0.00005, Orthogonality desired between the function vector and the columns of the Jacobian.
    xtol - default to None, Relative error desired in the approximate solution.
    maxfev - default to 50000, Only change if you are consistenly catching runtime errors and loosening gtol/ftols are not making a good enough fit.
    method - default is 'trf', see https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html#scipy.optimize.least_squares for description of methods
    Note: may need stricter tolerances on ftol/gtol for noisier data. Anecdotally, a stricter gtol (as low as 1e-8) has a larger effect on the quality of the bgsub.
    mask - Boolean mask defines non-vacuum region in SI, used to improve LCPL
    threshold - mininum average counts in fit window to be included in LCPL calculation.

    Outputs:
    if lcpl == False:
        bg_pl_SI - background subtracted SI
    if lcpl == True:
        bg_pl_SI, bg_lcpl_SI - background subtracted SI, LCPL background subtracted SI
    """

    fit_start_ch, fit_end_ch = np.searchsorted( energy, edge.e_bsub)
    si = si.astype('float32')
    if len(np.shape(si)) == 2:
        tempx,tempz = np.shape(si)
        si = np.reshape(si,(tempx,1,tempz))
    if len(np.shape(si)) == 1:
        tempz = len(si)
        si = np.reshape(si,(1,1,tempz))
    xdim, ydim, zdim = np.shape(si)


    ## Apply Local Background Averaging
    if (lba==True) and (gfwhm is not None)and (gfwhm>0):
        fit_data = prepare_lba( si, gfwhm, fit_start_ch, fit_end_ch )
    else:
        fit_data = si
    
    ## If log fitting or linear fitting, find fit using qr factorization       
    if log or (fit=='lin'):
        bg_pl_SI, fit_params = bgsub_SI_linearized( fit_data, energy, edge, fit=fit )

    ## Power law non-linear curve fitting using scipy.optimize.curve_fit
    elif (fit=='pl') or (fit=='exp') : 
        bg_pl_SI, fit_params = bgsub_SI_nllsq( fit_data, energy, edge, fit=fit, 
                                        maxfev=maxfev, method=method,
                                        ftol=ftol, gtol=gtol, xtol=xtol )
        
    ## Special case: if there is vacuum in the SI and it is causing trouble with your LCPL fitting:
    if mask is None and threshold is not None:
        mean_back = np.mean(si[:,:,fit_start_ch:fit_end_ch],axis=2)
        mask = mean_back > threshold
    elif mask is None and threshold is None:
        mask = np.ones((xdim,ydim), dtype='bool')
    
    maskline = np.reshape( mask,(xdim*ydim))
    rline_long = -1*np.reshape( fit_params[1,:,:], (xdim*ydim) )
    rline = rline_long[maskline]

    ## Given r values of SI, refit background using a linear combination of power laws, 
    ## using either 5/95 percentile or 20/80 percentile r values.
    if lc and (fit=='pl') or (fit=='exp'):
        bg_lcpl_SI = bgsub_SI_LC(fit_data, energy, edge, rline, fit=fit, perc=perc)
        return bg_pl_SI, bg_lcpl_SI
    else:
        return bg_pl_SI
    

def prepare_lba( si, gfwhm, fit_start_ch, fit_end_ch ):
    lba_raw = np.copy( si )
    lba_normalized = np.copy( si )
    for energychannel in np.arange(fit_start_ch,fit_end_ch):
        lba_raw[:,:,energychannel] = gaussian_filter(si[:,:,energychannel],sigma=gfwhm/2.35)
    
    lba_mean = np.mean( lba_raw[:,:,fit_start_ch:fit_end_ch], 2 )
    data_mean = np.mean(     si[:,:,fit_start_ch:fit_end_ch], 2)

    for energychannel in np.arange(fit_start_ch,fit_end_ch):
        lba_normalized[:,:,energychannel] = lba_raw[:,:,energychannel]*data_mean/lba_mean

    return lba_normalized
    

def linear_regression_QR( y, X ):
    # Solve Linear Regression using QR Decomposition
    # Y = Xb + error
    # R*b = Q.T*y minimizes MSE
    # y: (nx1) dependent variable
    # x: (nx1) independent variable
    # b: (2x1) [[b0],[b1]], b0: intercept, b1: slope
    if y.ndim == 1:
        Y = np.atleast_2d( y ).T
    else:
        Y = y

    Q, R = LA.qr(X)
    b = LA.inv(R) @ (Q.T @ Y)

    return b






########### background subtractions ########
def bgsub_SI_fast( si, energy, edge, rval, fit='pl'):
    """
    Quick background subtraction based on fixed 'r' value
    For Y = Ax + b + error with fixed 'A':
        Y' = b + error. MSE is minimized when b = mean(Y)
    """
    xdim, ydim, zdim = np.shape( si )

    fit_start_ch, fit_end_ch = np.searchsorted(energy, edge.e_bsub)
    y_win = si[:,:,fit_start_ch:fit_end_ch]
    e_win = np.reshape( energy[fit_start_ch:fit_end_ch], (1,1,(fit_end_ch-fit_start_ch)) )
    e_sub = np.reshape( energy[fit_start_ch:], (1,1,zdim-fit_start_ch) )

    bg_SI = np.zeros_like( si )  

    if fit == 'lin':
        c_fit = np.reshape( np.mean( y_win-rval*e_win, axis=(2)), (xdim,ydim,1))
        y_fit = c_fit + rval*e_sub

    if fit == 'pl':
        c_fit = np.reshape( np.mean( np.log(y_win)-rval*np.log(e_win), axis=(2)), (xdim,ydim,1))
        y_fit = np.exp( c_fit + rval*np.log(e_sub) )

    if fit == 'exp':
        c_fit = np.reshape( np.mean( np.log(y_win)-rval*e_win, axis=(2)), (xdim,ydim,1))
        y_fit = np.exp( c_fit + rval*e_sub )

    bg_SI[:,:,fit_start_ch:] = si[:,:,fit_start_ch:] - y_fit
    return bg_SI

def bgsub_SI_linearized( si, energy, edge, fit='pl'):
    """
    Quick background subtraction based on fixed 'r' value
    For Y = Ax + b + error with fixed 'A':
        Y' = b + error. MSE is minimized when b = mean(Y)
    """

    fit_start_ch, fit_end_ch = np.searchsorted(energy, edge.e_bsub)
    if (fit_end_ch - fit_start_ch)<2:
        fit_end_ch = fit_start_ch+2
    e_win = np.atleast_2d( energy[fit_start_ch:fit_end_ch] ).T
    e_sub = np.atleast_2d( energy[fit_start_ch:] ).T
    zdim = len(energy)

    if si.ndim == 1:
        si = np.reshape( si, (1,1,zdim))
    elif si.ndim == 2:
        (nx,nz) = si.shape
        si = np.reshape( si,(nx,1,zdim))

    xdim, ydim, zdim = np.shape( si )
    y_win = si[:,:,fit_start_ch:fit_end_ch]
    y_win = np.reshape( y_win, (xdim*ydim, len(e_win))).T

    bg_SI = np.zeros_like( si )  
    if fit == 'lin':
        e_win = np.insert( e_win, 0, 1, axis=1)
        e_sub = np.insert( e_sub, 0, 1, axis=1)

        b_fit = linear_regression_QR( y_win, e_win)
        y_fit = e_sub @ b_fit

    if fit == 'pl':
        e_win = np.insert( np.log(e_win), 0, 1, axis=1)
        e_sub = np.insert( np.log(e_sub), 0, 1, axis=1)

        b_fit = linear_regression_QR( np.log(y_win), e_win )
        y_fit = np.exp(e_sub @ b_fit )
        
        b_fit[0,:] = np.exp( b_fit[0,:] )

    if fit == 'exp':
        e_win = np.insert( e_win, 0, 1, axis=1)
        e_sub = np.insert( e_sub, 0, 1, axis=1)

        b_fit = linear_regression_QR( np.log(y_win), e_win )
        y_fit = np.exp(e_sub @ b_fit )

        b_fit[0,:] = np.exp( b_fit[0,:] )

    b_fit = np.squeeze( np.reshape( b_fit, (2,xdim,ydim)) )
    y_fit = np.reshape( y_fit.T, (xdim,ydim,len(e_sub)))
    bg_SI[:,:,fit_start_ch:] = si[:,:,fit_start_ch:] - y_fit
    bg_SI = np.squeeze( bg_SI )
    return bg_SI, b_fit

def bgsub_SI_nllsq( si, energy, edge, fit='pl',
                    maxfev=50000, method='trf',
                    ftol=0.0005, gtol=0.00005, xtol=None):
    """
    Quick background subtraction based on fixed 'r' value
    For Y = Ax + b + error with fixed 'A':
        Y' = b + error. MSE is minimized when b = mean(Y)
    """

    fit_start_ch, fit_end_ch = np.searchsorted(energy, edge.e_bsub)
    e_win = energy[fit_start_ch:fit_end_ch]
    e_sub = energy[fit_start_ch:]
    zdim = len(energy)

    if si.ndim == 1:
        si = np.reshape( si, (1,1,zdim))
    elif si.ndim == 2:
        (nx,nz) = si.shape
        si = np.reshape( si,(nx,1,zdim))

    xdim, ydim, zdim = np.shape( si )
    y_win = si[:,:,fit_start_ch:fit_end_ch]
    bg_SI = np.zeros_like( si )

    if fit == 'pl':
        fitfunc = ls.powerlaw
        fit_params = np.zeros( (2,xdim,ydim) )
    elif fit == 'exp':
        fitfunc = ls.exponential
        fit_params = np.zeros( (2,xdim,ydim) )

    mean_spec = np.mean( y_win, (0,1) )
    popt_init,pcov_init = curve_fit( fitfunc, e_win, mean_spec, maxfev=maxfev,method=method,verbose=0 )
    
    pbar1 = tqdm(total = (xdim)*(ydim),desc = "Background subtracting")
    for i in range(xdim):
        for j in range(ydim):
            popt_pl,pcov_pl=curve_fit( fitfunc, e_win, y_win[i,j,:],p0=popt_init,
                                    maxfev=maxfev,method=method,verbose = 0,
                                    ftol=ftol, gtol=gtol, xtol=xtol)
            
            bg_SI[i,j,fit_start_ch:] = si[i,j,fit_start_ch:] - fitfunc(energy[fit_start_ch:], *popt_pl)
            fit_params[:,i,j] = popt_pl
            pbar1.update(1)

    return bg_SI, fit_params

def bgsub_SI_LC( si, energy, edge, rline, fit='pl', perc=(5,95)):
    bg_lcpl_SI = np.zeros_like(si)
    rmu,rstd = norm.fit(rline)

    rmin = norm.ppf( perc[0]*0.01, rmu, rstd )
    rmax = norm.ppf( perc[1]*0.01, rmu, rstd )


    (xdim, ydim, zdim) = si.shape
    fit_start_ch, fit_end_ch = np.searchsorted(energy, edge.e_bsub)

    
    if fit=='pl':
        fitname = 'power law'
        e_win = np.log(energy[fit_start_ch:fit_end_ch])
        e_sub = np.log(energy[fit_start_ch:])
    elif fit=='exp':
        fitname = 'exponential'
        e_win = energy[fit_start_ch:fit_end_ch]
        e_sub = energy[fit_start_ch:]

    print( '{}th percentile {} = {}'.format( perc[0], fitname, rmin))
    print('{}th percentile {} = {}'.format( perc[1], fitname, rmax))

    len_e_win = len(e_win)
    len_e_sub = len(e_sub)

    e_win = np.atleast_2d( energy[fit_start_ch:fit_end_ch] ).T
    e_win = np.append( e_win**(-rmin), e_win**(-rmax), axis=1)
    e_sub = np.atleast_2d( energy[fit_start_ch:] ).T
    e_sub = np.append( e_sub**(-rmin), e_sub**(-rmax), axis=1)
    
    y_win = np.reshape( si[:,:,fit_start_ch:fit_end_ch], (xdim*ydim,len_e_win ) ).T
    
    
    b_fit = linear_regression_QR( y_win, e_win )
    y_fit = (e_sub @ b_fit).T

    bgndLCPL = np.reshape( y_fit,(xdim,ydim,len_e_sub))
    bg_lcpl_SI[:,:,fit_start_ch:] = si[:,:,fit_start_ch:] - bgndLCPL

    return bg_lcpl_SI


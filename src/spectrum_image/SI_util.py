import matplotlib.pyplot as plt
import numpy as np
import copy
from scipy.optimize import curve_fit
from scipy.ndimage import affine_transform
from sklearn.decomposition import PCA
from tqdm import tqdm, tqdm_notebook

from lmfit import Parameters, Minimizer

def remove_outlier( si, threshold_multiplier=5, remove_nn=True):
    # remove outliers that are larger than threshold_multiplier*std + median of each spectrum
    # remove_nn also remove two nearest neighbor pixels
    # Outliers are replaced by medians
    (ny,nx,ne) = si.shape
    si_cleaned = si.copy()
    fig, ax = plt.subplots(1)
    for i in range(ny):
        for j in range(nx):
            cur_spec = si_cleaned[i,j,:]
            med = np.median(cur_spec)
            mean = np.mean(cur_spec)
            std = np.std( cur_spec)

            if std > med:
                ind_outliers, = np.where( cur_spec>(med+threshold_multiplier*std) )
                for ind_outlier in ind_outliers:
                    ind_outlier = int( ind_outlier )
                    if remove_nn:
                        cur_spec[ ind_outlier-1:ind_outlier+2] = med
                    else:
                        cur_spec[ ind_outlier] = med
                si_cleaned[i,j,:] = cur_spec
                plt.plot(cur_spec)
    
    return si_cleaned


def get_hyperspy_data(hs_si):
    params=hs_si.axes_manager
    print(params)
    ch1=np.round(hs_si.axes_manager[2j].get_axis_dictionary()['offset'],4)
    disp=np.round(hs_si.axes_manager[2j].get_axis_dictionary()['scale'],4)
    hs_si.z=int(hs_si.axes_manager[2j].get_axis_dictionary()['size'])
    energy= np.round(np.arange(ch1,ch1+hs_si.z*disp,disp),4)
    pxscale = hs_si.axes_manager[0].get_axis_dictionary()['scale']
    if len(energy)!= hs_si.z:
        energy = energy[:-1]
    return(energy, hs_si.data, pxscale, disp, params)

def shear_y_SI( si, ADF=None, angle=0 ):
    # angle = shear angle in degree
    if angle == 0:
        if ADF is not None:
            return si, ADF
        else:
            return si


    a = np.tan( angle*np.pi/180 )
    shear_matrix_si = [[1, a, 0],[0, 1, 0],[0, 0, 1]]
    si_shear = affine_transform(si, shear_matrix_si, order=1)
    if ADF is not None:
        shear_matrix_ADF = [[1, a],[0, 1]]
        ADF_shear =affine_transform(ADF, shear_matrix_ADF, order=1)
        return si_shear, ADF_shear
    else:
        return si_shear


def shear_x_SI( si, ADF=None, angle=0 ):
    # angle = shear angle in degree
    if angle == 0:
        if ADF is not None:
            return si, ADF
        else:
            return si
    a = np.tan( angle*np.pi/180 )
    shear_matrix_si = [[1, 0, 0],[a, 1, 0],[0, 0, 1]]
    si_shear = affine_transform(si, shear_matrix_si, order=1)
    if ADF is not None:
        shear_matrix_ADF = [[1, 0],[a, 1]]
        ADF_shear =affine_transform(ADF, shear_matrix_ADF, order=1)
        return si_shear, ADF_shear
    else:
        return si_shear
    

def lorentzian( params, x, data=None ):

    A = params['A']
    e0 = params['e0']
    gm = params['gm']
    
    model = A/( ((x-e0)/gm)**2 + 1 )
    if data is None:
        return model
    return model-data

def gaussian( params, x, data=None ):
    A  = params['A']
    e0 = params['e0']
    gm = params['gm']
    sg = gm/(2*np.log(2))
    
    model = A*np.exp( -0.5*( ((x-e0)/sg)**2 ) )
    if data is None:
        return model
    return model-data

def fit_zeroloss_si( si, es, pk_func=gaussian, ftol = 1e-5 ):
    (ny, nx, ne) = si.shape

    e_bound = (-3,3)
    e_bound_ind = ( np.argmin( np.abs( es-e_bound[0] )),np.argmin( np.abs( es-e_bound[1] )) )

    e_fit = es[  e_bound_ind[0]:e_bound_ind[1] ]
    si_fit = si[ :,:, e_bound_ind[0]:e_bound_ind[1] ].copy()



    A0s = np.zeros( (ny,nx) )
    e0s = np.zeros( (ny,nx) )
    gms = np.zeros( (ny,nx) )


    pbar = tqdm_notebook(total = (nx)*(ny),desc = "Fitting Zeroloss Peak")
    for i in range(ny):
        for j in range(nx):
            cur_spec = si_fit[i,j]

            ind_max = np.argmax( cur_spec )
            A0 = cur_spec[ind_max]
            e0 = e_fit[ind_max]
            ind_hm = np.argmin( np.abs( cur_spec-A0/2 ) )
            gm0 = np.abs( e_fit[ind_max] - e_fit[ind_hm] )

            params = Parameters()
            params.add('A',  value=A0, min=A0/2, max=A0*2)
            params.add('e0', value=e0, min=e0-2, max=e0+2)
            params.add('gm', value=gm0, min=gm0/2, max=gm0*2)

            # try:
            min = Minimizer( pk_func, params, fcn_args=(e_fit,), fcn_kws={'data': cur_spec})
            out = min.leastsq()
                # out = min.leastsq(Dfun=d_func, col_deriv=1)
            # except:
            #     fig,ax = plt.subplots(1)
            #     plt.plot( e_fit, cur_spec )
            
            A0s[i,j] = out.params['A'].value
            e0s[i,j] = out.params['e0'].value
            gms[i,j] = out.params['gm'].value

            # if i ==0 and j==0 :
            #     fig,ax = plt.subplots(1)
            #     plt.plot( e_fit, cur_spec )
            #     plt.plot( e_fit, pk_func( e_fit, *popt_pl ))
            #     plt.vlines( [0,e0s[0,0]], 0, A0s[i,j])
            #     ax.set_xlim(-5,5)
            #     return
            
            pbar.update(1)


    return A0s, e0s, gms


def shift_zeroloss_SI( si, es, shifts ):
    (ny, nx, ne) = si.shape
    si_shifted = si.copy()

    dispersion = es[1]-es[0]
    shifts_ind = shifts/dispersion

    ke = ( np.arange( ne ) - ne/2 )*(2*np.pi/ne)


    pbar = tqdm_notebook(total = (nx)*(ny),desc = "Shifting Zeroloss Peak")
    for i in range(ny):
        for j in range(nx):
            cur_spec = si[i,j]
            cur_shift = shifts_ind[i,j]

            spec_fft = np.fft.fftshift( np.fft.fft(cur_spec) )
            result = spec_fft*np.exp( -1j*ke*cur_shift )
            spec_shifted = np.real( np.fft.ifft( np.fft.ifftshift( result) ) )


            # spec_shifted = shift(cur_spec, cur_shift, order=1, mode='constant', cval=0.0, prefilter=False)
            si_shifted[i,j] = spec_shifted
            pbar.update(1)

    min_shift = int( np.floor( np.min( shifts_ind )) )
    max_shift = int( np.ceil( np.max( shifts_ind )) )

    if min_shift >=0:
        min_shift = -1
    if max_shift <0:
        max_shift = 0
    
    # min_shift -=1
    # max_shift +=1

    # print( min_shift, max_shift )

    si_shifted =si_shifted[:,:, max_shift:min_shift]
    es_shifted =es[ max_shift:min_shift]
    return si_shifted, es_shifted

def PCA_show_scree( si ):
    # Convert to 2D Matrix for Decomposition and Normalize
    (ny,nx,ne) = si.shape
    data = si.copy()
    data = data.reshape(nx*ny,ne ).T
    data = (data - np.min(data)) / np.ptp(data)

    # Skree Plot for determine number of principle components
    pca = PCA().fit(data)

    fig, ax = plt.subplots(1)
    plt.plot(pca.explained_variance_ratio_[0:50], '-o', linewidth=2, c='black')
    plt.xlabel('Number of components', fontsize = 16)
    plt.ylabel('Explained variance', fontsize = 16)
    plt.tick_params(labelsize = 14)
    plt.yscale("log")
    plt.show()

def PCA_filter( si, n_components):

    # Convert to 2D Matrix for Decomposition and Normalize
    (ny,nx,ne) = si.shape
    data = si.copy()
    data = data.reshape(nx*ny,ne ).T

    data_min = np.min(data)
    data_range = np.ptp(data)
    data = (data - data_min) / data_range

    pca = PCA(n_components=n_components).fit(data)
    components = pca.transform(data)
    filtered = pca.inverse_transform(components).T
    si_pca = np.reshape(filtered, (ny,nx,ne))

    si_pca = si_pca*data_range + data_min

    return si_pca, components
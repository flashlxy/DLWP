#
# Copyright (c) 2017-18 Jonathan Weyn <jweyn@uw.edu>
#
# See the file LICENSE for your rights.
#

"""
Tools for pre-processing model input data into training/validation/testing data.
"""

import numpy as np
import netCDF4 as nc
import xarray as xr
import os

# netCDF fill value
fill_value = np.array(nc.default_fillvals['f4']).astype(np.float32)


def delete_nan_samples(predictors, targets, large_fill_value=False, threshold=None):
    """
    Delete any samples from the predictor and target numpy arrays and return new, reduced versions.

    :param predictors: ndarray, shape [num_samples,...]: predictor data
    :param targets: ndarray, shape [num_samples,...]: target data
    :param large_fill_value: bool: if True, treats very large values (> 1e30) as NaNs
    :param threshold: float 0-1: if not None, then removes any samples with a fraction of NaN larger than this
    :return: predictors, targets: ndarrays with samples removed
    """
    if threshold is not None and not (0 <= threshold <= 1):
        raise ValueError("'threshold' must be between 0 and 1")
    if large_fill_value:
        predictors[(predictors > 1.e30) | (predictors < -1.e30)] = np.nan
        targets[(targets > 1.e30) | (targets < -1.e30)] = np.nan
    p_shape = predictors.shape
    t_shape = targets.shape
    predictors = predictors.reshape((p_shape[0], -1))
    targets = targets.reshape((t_shape[0], -1))
    if threshold is None:
        p_ind = list(np.where(np.isnan(predictors))[0])
        t_ind = list(np.where(np.isnan(targets))[0])
    else:
        p_ind = list(np.where(np.mean(np.isnan(predictors), axis=1) >= threshold)[0])
        t_ind = list(np.where(np.mean(np.isnan(targets), axis=1) >= threshold)[0])
    bad_ind = list(set(p_ind + t_ind))
    predictors = np.delete(predictors, bad_ind, axis=0)
    targets = np.delete(targets, bad_ind, axis=0)
    new_p_shape = (predictors.shape[0],) + p_shape[1:]
    new_t_shape = (targets.shape[0],) + t_shape[1:]
    return predictors.reshape(new_p_shape), targets.reshape(new_t_shape)


class Preprocessor(object):

    def __init__(self, data_obj, predictor_file='.predictors.nc'):
        """
        Initialize an instance of Preprocessor for DLWP modelling. The data_obj is an instance of one of the data
        processing classes in DLWP.data, and should have data already loaded.

        :param data_obj: instance of DLWP.data class
        :param predictor_file: str: file to which to write the predictors and targets
        """
        self.raw_data = data_obj
        if self.raw_data.Dataset is None:
            print('Preprocessor warning: opening data with default args')
            self.raw_data.open()
        self._predictor_file = predictor_file
        self.data = None
        self._predictor_shape = ()

    def data_to_samples(self, variables='all', levels='all', in_memory=False, verbose=False):
        # Test that data is loaded
        if self.raw_data.Dataset is None:
            raise IOError('no data loaded to data_obj')

        # Convert variables and levels to appropriate type
        vars_available = list(self.raw_data.Dataset.data_vars.keys())
        if variables == 'all':
            variables = [v for v in vars_available]
        elif not(isinstance(variables, list) or isinstance(variables, tuple)):
            variables = [variables]
        if levels == 'all':
            levels = list(self.raw_data.Dataset.level.values)
        elif not(isinstance(levels, list) or isinstance(levels, tuple)):
            levels = [levels]

        # Get the exact dataset we want (index times, variables, and levels)
        all_dates = self.raw_data.dataset_dates
        ds = self.raw_data.Dataset.sel(time=all_dates, level=levels)
        if verbose:
            print('Preprocessor.data_to_samples: opening and formatting raw data')
        for v in vars_available:
            if v not in variables:
                ds = ds.drop(v)
        n_sample, n_var, n_level, n_lat, n_lon = (len(all_dates) - 1, len(variables), len(levels),
                                                  ds.dims['lat'], ds.dims['lon'])
        self._predictor_shape = (n_var, n_level, n_lat, n_lon)

        # Sort into predictors and targets. If in_memory is false, write to netCDF.
        if not in_memory:
            if os.path.isfile(self._predictor_file):
                raise IOError("predictor file '%s' already exists" % self._predictor_file)
            if verbose:
                print('Preprocessor.data_to_samples: creating output file %s' %self._predictor_file)
            nc_fid = nc.Dataset(self._predictor_file, 'w')
            nc_fid.description = 'Training data for DLWP'
            nc_fid.createDimension('sample', 0)
            nc_fid.createDimension('variable', n_var)
            nc_fid.createDimension('level', n_level)
            nc_fid.createDimension('lat', n_lat)
            nc_fid.createDimension('lon', n_lon)

            # Create spatial coordinates
            nc_var = nc_fid.createVariable('level', np.float32, 'level')
            nc_var.setncatts({
                'long_name': 'Pressure level',
                'units': 'hPa'
            })
            nc_fid.variables['level'][:] = levels

            nc_var = nc_fid.createVariable('lat', np.float32, 'lat')
            nc_var.setncatts({
                'long_name': 'Latitude',
                'units': 'degrees_north'
            })
            nc_fid.variables['lat'][:] = ds['lat'].values

            nc_var = nc_fid.createVariable('lon', np.float32, 'lon')
            nc_var.setncatts({
                'long_name': 'Longitude',
                'units': 'degrees_east'
            })
            nc_fid.variables['lon'][:] = ds['lon'].values

            # Create predictors and targets variables
            predictors = nc_fid.createVariable('predictors', np.float32, ('sample', 'variable', 'level', 'lat', 'lon'))
            predictors.setncatts({
                'long_name': 'Predictors',
                'units': 'N/A',
                '_FillValue': fill_value
            })
            targets = nc_fid.createVariable('targets', np.float32, ('sample', 'variable', 'level', 'lat', 'lon'))
            targets.setncatts({
                'long_name': 'Targets',
                'units': 'N/A',
                '_FillValue': fill_value
            })

        else:
            # Load all the data for speed... better be careful
            if verbose:
                print('Preprocessor.data_to_samples: loading data to memory')
            ds.load()
            predictors = np.full((n_sample, n_var, n_level, n_lat, n_lon), np.nan, dtype=np.float32)
            targets = predictors.copy()

        # Fill in the data. Each point gets filled with the target index 1 higher
        for s in range(n_sample):
            if verbose:
                print('Preprocessor.data_to_samples: writing sample %s of %s' % (s+1, n_sample))
            for v, var in enumerate(variables):
                predictors[s, v, ...] = ds[var].isel(time=s).values
                targets[s, v, ...] = ds[var].isel(time=s+1).values

        if not in_memory:
            nc_fid.close()
            result_ds = xr.open_dataset(self._predictor_file)
        else:
            result_ds = xr.Dataset({
                'predictors': (['sample', 'variable', 'level', 'lat', 'lon'], predictors, {
                    'long_name': 'Predictors',
                    'units': 'N/A'
                }),
                'targets': (['sample', 'variable', 'level', 'lat', 'lon'], targets, {
                    'long_name': 'Targets',
                    'units': 'N/A'
                }),
            }, coords={
                'variable': ('variable', variables),
                'level': ('level', levels, {
                    'long_name': 'Pressure level',
                    'units': 'hPa'
                }),
                'lat': ('lat', ds['lat'].values, {
                    'long_name': 'Latitude',
                    'units': 'degrees_north'
                }),
                'lon': ('lon', ds['lon'].values, {
                    'long_name': 'Longitude',
                    'units': 'degrees_east'
                }),
            }, attrs={
                'description': 'Training data for DLWP'
            })

        self.data = result_ds

    def open(self, **kwargs):
        """
        Open the dataset pointed to by the instance's _predictor_file attribute onto self.data

        :param kwargs: passed to xarray.open_dataset()
        """
        self.data = xr.open_dataset(self._predictor_file, **kwargs)

    def close(self):
        """
        Close the dataset on self.data
        """
        self.data.close()
        self.data = None

    def to_file(self, predictor_file=None):
        """
        Write the data opened on self.data to the file predictor_file if not None or the instance's _predictor_file
        attribute.

        :param predictor_file: str: file path; if None, uses self._predictor_file
        """
        if self.data is None:
            raise ValueError('cannot save to file with no sample data generated or opened')
        if predictor_file is None:
            predictor_file = self._predictor_file
        self.data.to_netcdf(predictor_file)
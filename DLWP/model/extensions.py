#
# Copyright (c) 2019 Jonathan Weyn <jweyn@uw.edu>
#
# See the file LICENSE for your rights.
#

"""
Extension classes for doing more with models, generators, and so on.
"""

import numpy as np
import xarray as xr
import pandas as pd
from .models import DLWPNeuralNet
from .models_torch import DLWPTorchNN
from .generators import DataGenerator, SmartDataGenerator, SeriesDataGenerator
from ..util import insolation


class TimeSeriesEstimator(object):
    """
    Sophisticated wrapper class for producing time series forecasts from a DLWP model, using a Generator with
    metadata. This class allows predictions with non-matching inputs and outputs, including in the variable, level, and
    time_step dimensions.
    """

    def __init__(self, model, generator):
        """
        Initialize a TimeSeriesEstimator from a model and generator.

        :param model: DLWP model instance
        :param generator: DLWP DataGenerator instance
        """
        if not isinstance(model, (DLWPNeuralNet, DLWPTorchNN)):
            raise TypeError("'model' must be a valid instance of a DLWP model class")
        if not isinstance(generator, (DataGenerator, SmartDataGenerator, SeriesDataGenerator)):
            raise TypeError("'generator' must be a valid instance of a DLWP generator class")
        self.model = model
        self.generator = generator
        self._add_insolation = generator._add_insolation if hasattr(generator, '_add_insolation') else False
        self._uses_varlev = 'varlev' in generator.ds.dims
        self._has_targets = 'targets' in generator.ds.variables
        self._is_series = isinstance(generator, SeriesDataGenerator)
        self._output_sel = {}
        self._input_sel = {}
        self._dt = self.generator.ds['sample'][1] - self.generator.ds['sample'][0]

        # Generate the selections needed for inputs and outputs
        if self._uses_varlev:
            # Find the outputs we keep
            if self._is_series:  # a SeriesDataGenerator has user-specified I/O
                if not self.generator._output_sel:  # selection was empty
                    self._output_sel = {'varlev': np.array(self.generator.ds.coords['varlev'][:])}
                else:
                    self._output_sel = {k: np.array(v) for k, v in self.generator._output_sel.items()}
            else:
                if self._has_targets:
                    self._output_sel = {'varlev': np.array(self.generator.ds.targets.coords['varlev'][:])}
                else:
                    self._output_sel = {'varlev': np.array(self.generator.ds.coords['varlev'][:])}
            # Find the inputs we need
            if self._is_series:
                if not self.generator._input_sel:  # selection was empty
                    self._input_sel = {'varlev': np.array(self.generator.ds.coords['varlev'][:])}
                else:
                    self._input_sel = {k: np.array(v) for k, v in self.generator._input_sel.items()}
            else:
                self._input_sel = {'varlev': np.array(self.generator.ds.predictors.coords['varlev'][:])}
            # Find outputs that need to replace inputs
            self._outputs_in_inputs = {
                'varlev': np.array([v for v in self._output_sel['varlev'] if v in self._input_sel['varlev']])
            }
        else:  # Uses variable/level coordinates
            # Find the outputs we keep
            if self._is_series:  # a SeriesDataGenerator has user-specified I/O
                if not self.generator._output_sel:  # selection was empty
                    self._output_sel = {'variable': np.array(self.generator.ds.coords['variable'][:]),
                                        'level': np.array(self.generator.ds.coords['level'][:])}
                else:
                    self._output_sel = {k: np.array(v) for k, v in self.generator._output_sel.items()}
                    if 'variable' not in self._output_sel.keys():
                        self._output_sel['variable'] = np.array(self.generator.ds.coords['variable'][:])
                    if 'level' not in self._output_sel.keys():
                        self._output_sel['level'] = np.array(self.generator.ds.coords['level'][:])
            else:
                if self._has_targets:
                    self._output_sel = {'variable': np.array(self.generator.ds.targets.coords['variable'][:]),
                                        'level': np.array(self.generator.ds.targets.coords['level'][:])}
                else:
                    self._output_sel = {'variable': np.array(self.generator.ds.coords['variable'][:]),
                                        'level': np.array(self.generator.ds.coords['level'][:])}
            # Find the inputs we need
            if self._is_series:
                if not self.generator._input_sel:  # selection was empty
                    self._input_sel = {'variable': np.array(self.generator.ds.coords['variable'][:]),
                                       'level': np.array(self.generator.ds.coords['level'][:])}
                else:
                    self._input_sel = {k: np.array(v) for k, v in self.generator._input_sel.items()}
                    if 'variable' not in self._input_sel.keys():
                        self._input_sel['variable'] = np.array(self.generator.ds.coords['variable'][:])
                    if 'level' not in self._input_sel.keys():
                        self._input_sel['level'] = np.array(self.generator.ds.coords['level'][:])
            else:
                self._input_sel = {'variable': np.array(self.generator.ds.predictors.coords['variable'][:]),
                                   'level': np.array(self.generator.ds.predictors.coords['level'][:])}
            # Flatten variable/level
            lev, var = np.meshgrid(self._input_sel['level'], self._input_sel['variable'])
            varlev = np.array(['/'.join([v, str(l)]) for v, l in zip(var.flatten(), lev.flatten())])
            self._input_sel['varlev'] = varlev
            lev, var = np.meshgrid(self._output_sel['level'], self._output_sel['variable'])
            varlev = np.array(['/'.join([v, str(l)]) for v, l in zip(var.flatten(), lev.flatten())])
            self._output_sel['varlev'] = varlev
            # Find outputs that need to replace inputs
            self._outputs_in_inputs = {
                'variable': np.array([v for v in self._output_sel['variable'] if v in self._input_sel['variable']]),
                'level': np.array([v for v in self._output_sel['level'] if v in self._input_sel['level']]),
                'varlev': np.array([v for v in self._output_sel['varlev'] if v in self._input_sel['varlev']])
            }
        if self._add_insolation:
            self._input_sel['varlev'] = np.concatenate([self._input_sel['varlev'], np.array(['SOL'])])

        # Time step dimension
        self._input_time_steps = (generator._input_time_steps if isinstance(generator, SeriesDataGenerator)
                                  else model.time_dim)
        self._output_time_steps = (generator._output_time_steps if isinstance(generator, SeriesDataGenerator)
                                   else model.time_dim)

    def predict(self, steps, impute=False, keep_time_dim=False, prefer_first_times=True, **kwargs):
        """
        Step forward the time series prediction from the model 'steps' times, feeding predictions back in as
        inputs. Predicts for all the data provided in the generator. If there are inputs which are not produced by
        the model outputs, we include the available inputs from the generator data and either reduce the number of
        predicted samples accordingly (remove those whose inputs cannot be satisfied) or run the model using the mean
        values of the inputs which cannot be satisfied. If there are fewer output time steps than input time steps,
        then we build a time series forecast intelligently using part of the predictors and part of the prediction at
        every step. Note only the SeriesDataGenerator supports variable inputs/outputs.

        :param steps: int: number of times to step forward
        :param impute: bool: if True, use the mean state for missing inputs in the forward integration
        :param keep_time_dim: bool: if True, keep the time_step dimension instead of integrating it with forecsat_hour
            to produce a continuous time series
        :param prefer_first_times: bool: in the case where the prediction contains more time_steps than the input,
            use the first available predicted times to initialize the next step, otherwise use the last times. If the
            output time_steps is less than the input time_steps, we always use all of the output times.
        :param kwargs: passed to Keras.predict()
        :return: ndarray: predicted states with forecast_step as the first dimension
        """
        if int(steps) < 1:
            raise ValueError('must use positive integer for steps')

        # Effective forward time steps for each step
        if self._output_time_steps <= self._input_time_steps:
            keep_inputs = True
            es = self._output_time_steps
            in_times = np.arange(self._input_time_steps) - (self._input_time_steps - self._output_time_steps)
        else:
            keep_inputs = False
            if prefer_first_times:
                es = self._input_time_steps
                in_times = np.arange(self._input_time_steps)
            else:
                es = self._output_time_steps
                in_times = np.arange(self._input_time_steps) + (self._output_time_steps - self._input_time_steps)

        # Load data from the generator
        p, t = self.generator.generate([])
        p_shape = tuple(p.shape)

        # Add metadata
        p = p.reshape((p_shape[0], self._input_time_steps, -1,) + self.generator.convolution_shape[-2:])
        p_da = xr.DataArray(
            p,
            coords=[self.generator.ds.sample[:self.generator._n_sample], in_times,
                    self._input_sel['varlev'], self.generator.ds.lat, self.generator.ds.lon],
            dims=['sample', 'time_step', 'varlev', 'lat', 'lon']
        )

        # Calculate mean for imputing
        if impute:
            p_mean = p.mean(axis=0)

        # Giant forecast array
        result = np.full((steps,) + t.shape, np.nan, dtype=np.float32)

        # Iterate prediction forward
        for s in range(steps):
            if 'verbose' in kwargs and kwargs['verbose'] > 0:
                print('Time step %d/%d' % (s + 1, steps))
            result[s] = self.model.predict(p_da.values.reshape(p_shape), **kwargs)

            # Add metadata to the prediction
            r_da = xr.DataArray(
                result[s].reshape((p_shape[0], self._output_time_steps, -1,) +
                                  self.generator.convolution_shape[-2:]),
                coords=[self.generator.ds.sample[:self.generator._n_sample] + es * self._dt,
                        np.arange(self._output_time_steps), self._output_sel['varlev'],
                        self.generator.ds.lat, self.generator.ds.lon],
                dims=['sample', 'time_step', 'varlev', 'lat', 'lon']
            )

            # Re-index the predictors to the new forward time step
            p_da = p_da.reindex(sample=r_da.sample, method=None)

            # Impute values extending beyond data availability
            if impute:
                # Calculate mean values for the added time steps after re-indexing
                p_da[-es:] = np.concatenate([p_mean[np.newaxis, ...]] * es)

            # Take care of the known insolation for added time steps
            if self._add_insolation:
                p_da.loc[{'varlev': 'SOL'}][-es:] = \
                    np.concatenate([insolation(p_da.sample[-es:] + n * self._dt,
                                               self.generator.ds.lat, self.generator.ds.lon)[:, np.newaxis]
                                    for n in range(self._input_time_steps)], axis=1)

            # Replace the predictors that exist in the result with the result. Any that do not exist are automatically
            # inherited from the known predictor data (or imputed data).
            if keep_inputs:
                loc_dict = dict(varlev=self._outputs_in_inputs['varlev'], time_step=p_da.time_step[-es:])
                p_da.loc[loc_dict] = r_da.loc[{'varlev': self._outputs_in_inputs['varlev']}]
            else:
                if prefer_first_times:
                    p_da.loc[{'varlev': self._outputs_in_inputs['varlev']}] = \
                        r_da.loc[{'varlev': self._outputs_in_inputs['varlev']}][:, :self._input_time_steps]
                else:
                    p_da.loc[{'varlev': self._outputs_in_inputs['varlev']}] = \
                        r_da.loc[{'varlev': self._outputs_in_inputs['varlev']}][:, -self._input_time_steps:]

        # Return a DataArray. Keep the actual model initialization, that is, the last available time in the inputs,
        # as the time
        result = result.reshape((steps, p_shape[0], self._output_time_steps, -1,) +
                                self.generator.convolution_shape[-2:])
        if keep_time_dim:
            result = xr.DataArray(
                result,
                coords=[
                    np.arange(self._dt.values, (steps * es + 1) * self._dt.values, es * self._dt.values),
                    self.generator.ds.sample[:self.generator._n_sample] + (self._input_time_steps - 1) * self._dt,
                    range(self._output_time_steps),
                    self._output_sel['varlev'],
                    self.generator.ds.lat,
                    self.generator.ds.lon
                ],
                dims=['f_hour', 'time', 'time_step', 'varlev', 'lat', 'lon']
            )
        else:
            # To create a correct time series, we must retain only the effective steps
            if not keep_inputs:
                if prefer_first_times:
                    result = result[:, :, :es]
            result = result.transpose((0, 2, 1, 3, 4, 5))
            result = result.reshape((-1,) + result.shape[2:])
            result = xr.DataArray(
                result,
                coords=[
                    np.arange(self._dt.values, (steps * es + 1) * self._dt.values, self._dt.values),
                    self.generator.ds.sample[:self.generator._n_sample] + (self._input_time_steps - 1) * self._dt,
                    self._output_sel['varlev'],
                    self.generator.ds.lat,
                    self.generator.ds.lon
                ],
                dims=['f_hour', 'time', 'varlev', 'lat', 'lon']
            )

        # Expand back out to variable/level pairs
        if self._uses_varlev:
            return result
        else:
            var, lev = self._output_sel['variable'], self._output_sel['level']
            vl = pd.MultiIndex.from_product((var, lev), names=('variable', 'level'))
            result = result.assign_coords(varlev=vl).unstack('varlev')
            result = result.transpose('f_hour', 'time', 'variable', 'level', 'lat', 'lon')
            return result

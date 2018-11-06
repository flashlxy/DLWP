#
# Copyright (c) 2017-18 Jonathan Weyn <jweyn@uw.edu>
#
# See the file LICENSE for your rights.
#

"""
Test conversion of CFS data into preprocessed predictors/targets for the DLWP model.
"""

from DLWP.data import CFSReanalysis
from DLWP.model import Preprocessor
from datetime import datetime
import pandas as pd

start_date = datetime(2005, 1, 1)
end_date = datetime(2009, 12, 31)
dates = list(pd.date_range(start_date, end_date, freq='D').to_pydatetime())
variables = ['HGT']
levels = [300, 500, 700]
data_root = '/home/disk/wave2/jweyn/Data'

cfs = CFSReanalysis(root_directory='%s/CFSR' % data_root, file_id='dlwp_')
cfs.set_dates(dates)
cfs.open(autoclose=True)

pp = Preprocessor(cfs, predictor_file='%s/DLWP/cfs_2000-2009_hgt_300-700.nc' % data_root)
pp.data_to_samples(variables=variables, levels=levels, verbose=True)
print(pp.data)
pp.close()
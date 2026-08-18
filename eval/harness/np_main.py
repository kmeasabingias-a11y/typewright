import os
os.chdir("/tmp")  # the only writable path, like execution.py's preamble
import numpy as np
import pandas as pd
import re
print("re:", re.sub("a", "b", "abc"))
print("numpy:", np.zeros(3).sum())
print("pandas:", pd.Series([1, 2, 3]).sum())

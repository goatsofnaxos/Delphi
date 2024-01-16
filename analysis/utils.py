import harp
import pandas as pd
from glob import glob
from pathlib import Path

def load(reader, root):
    root = Path(root)
    pattern = f"{root.joinpath(root.name)}_{reader.register.address}_*.bin"
    data = [reader.read(file) for file in glob(pattern)]
    return pd.concat(data)

import harp
import pandas as pd
from glob import glob
from pathlib import Path
import datetime
import json
from dotmap import DotMap
from aeon.io.reader import Reader
import aeon.io.api as api


def load_json(reader, root):
    root = Path(root)
    pattern = f"{root.joinpath(root.name)}_*.jsonl"
    data = [reader.read(Path(file)) for file in glob(pattern)]
    return pd.concat(data)


def load(reader, root):
    root = Path(root)
    pattern = f"{root.joinpath(root.name)}_{reader.register.address}_*.bin"
    data = [reader.read(file) for file in glob(pattern)]
    return pd.concat(data)


class SessionData(Reader):
    """Extracts metadata information from a settings .jsonl file."""

    def __init__(self, pattern="Metadata"):
        super().__init__(pattern, columns=["metadata"], extension="jsonl")

    def read(self, file):
        """Returns metadata for the specified epoch."""
        epoch_str = file.parts[-1]
        date_str, time_str = epoch_str.split("T")
        date_str = date_str.split("_")[1]
        time_str = time_str.split(".")[0]
        time = datetime.datetime.fromisoformat(date_str + "T" + time_str.replace("-", ":"))
        with open(file) as fp:
            metadata = [json.loads(line) for line in fp]

        data = {
            "metadata": [DotMap(entry['value']) for entry in metadata],
        }
        timestamps = [api.aeon(entry['seconds']) for entry in metadata]

        return pd.DataFrame(data, index=[timestamps], columns=self.columns)


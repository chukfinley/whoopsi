"""Common metric interfaces and base class for all algorithms."""

from dataclasses import dataclass
from abc import ABC, abstractmethod

import pandas as pd


@dataclass
class WhoopScores:
    """Computed scores matching Whoop's output."""
    date: str
    recovery: float    # 0-100 (%)
    sleep: float       # 0-100 (%)
    strain: float      # 0-21
    hrv_ms: float      # RMSSD in ms
    rhr_bpm: float     # Resting heart rate in BPM
    resp_rate: float   # Breaths per minute


class BaseAlgorithm(ABC):
    """Base class for all Whoop-replication algorithms."""

    name: str = "base"

    @abstractmethod
    def compute(self, sensor_df: pd.DataFrame, day) -> WhoopScores:
        """Compute Whoop scores for a single day from raw sensor data."""
        ...

    def compute_all(self, sensor_df: pd.DataFrame, dates: list) -> list[WhoopScores]:
        """Compute scores for multiple days."""
        results = []
        for day in dates:
            try:
                scores = self.compute(sensor_df, day)
                results.append(scores)
            except Exception as e:
                print(f"  {self.name}: Error on {day}: {e}")
        return results

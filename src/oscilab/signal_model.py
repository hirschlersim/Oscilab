from PyQt6 import QtWidgets as qtw
from PyQt6 import QtGui as qtg
from PyQt6 import QtCore as qtc

from loguru import logger
from scipy import signal
import numpy as np
from collections import deque


class SignalModel():


    def __init__(self, _label, _fs):
        self.label = _label
        self.fs = _fs
        self.data = deque(maxlen=1000)  # TODO after duration and samplingrate
        self.time = deque(maxlen=1000)
        self.sos = None  # Filter
        self.zi = None
        self.gain = None  # Scaling
        self.offset = None

    def add_data(self, _t, _x):

        if self.gain is not None:
            _x = (_x - self.scal.offset) * self.scal.gain

        if self.sos is not None:
            _x, self.zi = signal.sosfilt(sos=self.sos,
                                         x=np.asarray(_x),
                                         zi=self.zi)
        self.data.extend(_x)
        self.time.extend(_t)


    def get_lastTimestamp(self):
        return self.time[-1]


    def add_filter(self,):
        self.sos = signal.butter(N=3, Wn=50, btype='low', analog=False, fs=1000, output='sos')
        self.zi = signal.sosfilt_zi(self.filt)


    def rem_filter(self,):
        self.sos = None
        self.zi = None


    def add_scaling(self, _gain, _offset):
        self.gain = _gain
        self.offset = _offset


    def rem_scaling(self):
        self.gain = None
        self.offset = None

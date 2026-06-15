from PyQt6 import QtWidgets as qtw
from PyQt6 import QtGui as qtg
from PyQt6 import QtCore as qtc

from loguru import logger


class SignalModel():
    def __init__(self, label, fs):
        self.label = label
        self.fs = fs
        pass
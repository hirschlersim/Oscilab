import numpy as np
import matplotlib.pyplot as plt
from numba import njit

# 1. Core STFT optimized to C-speed using @njit
@njit(fastmath=True)
def _calc_stft_core(signal, window_size):
    """
    Processes a single 1D signal. Numba compiles this loop perfectly.
    """
    n_samples = len(signal)
    hop_size = 1 
    n_frames = n_samples - window_size + 1
    n_freqs = window_size // 2 + 1
    
    # Pre-allocate output array
    stft_matrix = np.zeros((n_freqs, n_frames), dtype=np.complex128)
    
    # Hanning window to prevent spectral leakage
    win = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(window_size) / (window_size - 1)))
    
    for t in range(n_frames):
        start = t * hop_size
        end = start + window_size
        frame = signal[start:end] * win
        
        # Compute Real FFT 
        full_fft = np.fft.fft(frame)
        stft_matrix[:, t] = full_fft[:n_freqs]
        
    return stft_matrix


# 2. Vectorized Wrapper using standard NumPy utilities
def get_spec(signals, window_size=100):
    """
    Vectorized wrapper. Handles 1D signals natively, and handles 
    2D batched signals (e.g. multiple channels) via NumPy vectorization.
    """
    signals = np.asarray(signals)
    
    # If it's just a single 1D signal, process it directly
    if signals.ndim == 1:
        return _calc_stft_core(signals, window_size)
    
    # If it's a 2D batch of signals, vectorize across the rows (axis 0)
    elif signals.ndim == 2:
        # signature instructs np.vectorize to take a 1D array and return a 2D matrix
        vfunc = np.vectorize(lambda s: _calc_stft_core(s, window_size), signature='(n)->(m,k)')
        return vfunc(signals)
    
    else:
        raise ValueError("Signal must be either 1D or 2D.")


if __name__ == "__main__":
    # --- 1. Generate Signal Data ---
    fs = 1000  # Sampling frequency
    time = np.linspace(start=0, stop=10, num=10_000)
    
    # Linear Chirp calculation (5Hz to 25Hz)
    f_start, f_end = 5, 25
    phase = 2 * np.pi * (f_start * time + (f_end - f_start) / 20 * time**2)
    phase = 2 * np.pi * time * np.linspace(start=10, stop=20, num=10_000)
    sign = np.sin(phase)
    
    window_size = 100

    # --- 2. Run the STFT ---
    # This calls our Numba-powered pipeline safely
    spectrogram = get_spec(sign, window_size)
    
    # --- 3. Setup Frequency and Time Axis for Plotting ---
    frequencies = np.fft.rfftfreq(n=window_size, d=1/fs)
    spec_db = 20 * np.log10(np.abs(spectrogram) + 1e-10)

    # --- 4. Plotting ---
    plt.figure(figsize=(10, 5))
    
    extent = [time[0], time[-1], frequencies[0], frequencies[-1]]
    plt.imshow(spec_db, cmap='magma', origin='lower', aspect='auto', extent=extent)
    
    plt.title("Numba STFT Spectrogram (Fixed Vectorization)")
    plt.xlabel("Time (seconds)")
    plt.ylabel("Frequency (Hz)")
    plt.colorbar(label="Magnitude (dB)")
    plt.ylim(0, 40)  # Zooming in on the 5Hz - 25Hz action
    plt.tight_layout()
    plt.show()
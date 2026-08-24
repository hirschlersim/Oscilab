import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import ShortTimeFFT
import numba


def get_stft_operator(fs, window_length=334):
    """Initializes and returns the ShortTimeFFT operator config."""
    w = np.ones(window_length)
    return ShortTimeFFT(
        win=w, hop=1, fs=fs, fft_mode="onesided", mfft=None, scale_to="magnitude"
    )


@numba.njit(cache=True)
def numba_stft(signal, window_len, hop_size):
    """A pure Numba-compiled sliding window STFT.

    Expects a 1D signal and uniform rectangular window (ones).
    """
    sig_len = len(signal)

    # Calculate how many frames/slices fit
    num_frames = (sig_len - window_len) // hop_size + 1
    if num_frames <= 0:
        return np.zeros((1, 1), dtype=np.float64)

    # rfft output size is (window_len // 2) + 1
    fft_bins = (window_len // 2) + 1
    stft_matrix = np.empty((fft_bins, num_frames), dtype=np.float64)

    # Simple uniform window array (equivalent to np.ones)
    # If you want a custom window, pass it as an argument to the function
    window = np.ones(window_len)

    for frame_idx in range(num_frames):
        start = frame_idx * hop_size
        end = start + window_len

        # Extract the segment and apply window
        segment = signal[start:end] * window

        # Numba fully supports standard 1D real FFTs
        fft_res = np.fft.rfft(segment)

        # Compute magnitude on the fly to save memory allocations
        for bin_idx in range(fft_bins):
            real = fft_res[bin_idx].real
            imag = fft_res[bin_idx].imag
            stft_matrix[bin_idx, frame_idx] = np.sqrt(real * real + imag * imag)

    return stft_matrix


def compute_stft(signal, SFT):
    """Core calculation function: Takes a signal array and the SFT operator,

    returns the magnitude spectrum and its spatial/time extent.
    """
    N = len(signal)
    Sx = SFT.stft(signal)
    magnitude = np.abs(Sx)
    extent = SFT.extent(N)
    return magnitude, extent, N


def plot_stft(magnitude, extent, N, SFT, title="", time_vector=None, freq_vector=None):
    """Handles only the visualization logic using pre-calculated data."""
    fig1, ax1 = plt.subplots(figsize=(6.0, 4.0))
    t_lo, t_hi = extent[:2]

    ax1.set_title(f"{title}")
    ax1.set(
        xlabel=f"Time $t$ in seconds ({SFT.p_num(N)} slices, $\Delta t = {SFT.delta_t:6.3f}\,$s)",
        ylabel=f"Freq. $f$ in Hz ({SFT.f_pts:6.3f} bins, $\Delta f = {SFT.delta_f:6.3f}\,$Hz)",
        xlim=(t_lo, t_hi),
    )

    im1 = ax1.imshow(magnitude, origin="lower", aspect="auto", extent=extent, cmap="viridis")
    fig1.colorbar(im1, label="Magnitude $|S_x(t, f)|$")

    if freq_vector is not None and time_vector is not None:
        ax1.plot(time_vector, freq_vector, "r--", alpha=0.5, label="$f_{synthetic}(t)$")
        ax1.legend()

    # Shade border regions
    for t0_, t1_ in [
        (t_lo, SFT.lower_border_end[0] * SFT.T),
        (SFT.upper_border_begin(N)[0] * SFT.T, t_hi),
    ]:
        ax1.axvspan(t0_, t1_, color="w", linewidth=0, alpha=0.2)

    for t_ in [0, N * SFT.T]:
        ax1.axvline(t_, color="y", linestyle="--", alpha=0.5)

    fig1.tight_layout()
    return fig1, ax1


# --- Example Usage & Live Update Loop Simulation ---
if __name__ == "__main__":
    # 1. Setup global parameters
    total_samples = 10_000
    time_array = np.linspace(start=0, stop=10, num=total_samples)
    f_array = np.linspace(start=5, stop=25, num=total_samples)
    full_signal = np.sin(2 * np.pi * time_array * f_array)

    fs = 1 / np.mean(np.diff(time_array))
    SFT = get_stft_operator(fs=fs, window_length=334)

    # 2. Simulate a live update loop receiving data chunks
    chunk_size = 2000
    current_idx = 0

    # This buffer accumulates incoming data that hasn't been calculated yet
    signal_buffer = np.array([])

    print("Simulating data update loop...")
    while current_idx < total_samples:
        # Simulate receiving a new chunk of data (e.g., from a sensor or stream)
        next_idx = min(current_idx + chunk_size, total_samples)
        new_data_chunk = full_signal[current_idx:next_idx]

        # Append new data to your rolling processing buffer
        signal_buffer = np.concatenate((signal_buffer, new_data_chunk))

        # Core Calculation runs here on the updated data
        magnitude, extent, N = numba_stft(signal_buffer, SFT, 1)

        print(f"Processed up to sample {next_idx}. Current STFT shape: {magnitude.shape}")

        current_idx = next_idx

    # 3. Plot the final result using the standalone plotting function
    plot_stft(
        magnitude,
        extent,
        N,
        SFT,
        title="Final Loop State",
        time_vector=time_array,
        freq_vector=f_array,
    )
    plt.show()
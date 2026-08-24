import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import ShortTimeFFT
from scipy.signal.windows import gaussian


def stft(signal, time, title="", freq=None):

    fs = 1 / np.mean(np.diff(time))
    N = len(signal)

    g_std = int(fs/4)  # standard deviation for Gaussian window in samples
    # w = gaussian(M=int(fs), std=g_std, sym=False)  # symmetric Gaussian window
    w = np.ones(334)
    SFT = ShortTimeFFT(win=w, hop=1, fs=fs, fft_mode='onesided', mfft=None, scale_to='magnitude')
    Sx = SFT.stft(signal)  # perform the STFT

    fig1, ax1 = plt.subplots(figsize=(6., 4.))  # enlarge plot a bit
    t_lo, t_hi = SFT.extent(N)[:2]  # time range of plot
    # ax1.set_title(rf"{title} ({SFT.m_num * SFT.T:5.3f}$\,s$ Gaussian window, " +
    #               rf"$\sigma_t={g_std * SFT.T:6.3f}\,$s)")
    ax1.set_title(f'{title}')
    ax1.set(xlabel=f"Zeit [s], " + rf"$\Delta t = {SFT.delta_t:6.3f}\,$s",
            ylabel=f"Frequenz [Hz], " + rf"$\Delta f = {SFT.delta_f:6.3f}\,$Hz",
            xlim=(t_lo, t_hi))

    im1 = ax1.imshow(abs(Sx), origin='lower', aspect='auto',
                     extent=SFT.extent(N), cmap='viridis')
    if freq is not None:  # academic we now the function and plot it over the stft
        ax1.plot(time, freq, 'r--', alpha=.5, label='$f_{Schätzung}(t)$')
    fig1.colorbar(im1, label="Amplitude $|S_x(t, f)|$")

    # Shade areas where window slices stick out to the side:
    for t0_, t1_ in [(t_lo, SFT.lower_border_end[0] * SFT.T),
                     (SFT.upper_border_begin(N)[0] * SFT.T, t_hi)]:
        ax1.axvspan(t0_, t1_, color='w', linewidth=0, alpha=.2)
    for t_ in [0, N * SFT.T]:  # mark signal borders with vertical line:
        ax1.axvline(t_, color='y', linestyle='--', alpha=0.5)
    if freq is not None:
        ax1.legend()
    fig1.tight_layout()
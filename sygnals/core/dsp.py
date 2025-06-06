# sygnals/core/dsp.py

"""
Core Digital Signal Processing (DSP) functions.

Includes FFT, STFT, CQT, Correlation, PSD, Convolution, Windowing, Envelope Detection etc.
Excludes specific filter implementations (see filters.py).
Uses scipy.fft for FFT/IFFT, librosa for STFT/CQT, and scipy.signal for others where appropriate.
"""

import logging
# Import necessary types
from typing import Tuple, Optional, Union, Literal, Any

import numpy as np
import librosa # Use librosa for STFT, CQT etc. for consistency and features
from numpy.typing import NDArray
from scipy.fft import fft, ifft, fftfreq # Use scipy.fft for basic FFT/IFFT
from scipy.signal import fftconvolve, get_window, hilbert, correlate, periodogram, welch

# Attempt absolute import for rms_energy at the top level
# This is needed for the 'rms' method in amplitude_envelope
try:
    # Assuming rms_energy is correctly placed in the audio features module
    from sygnals.core.audio.features import rms_energy
    _RMS_ENERGY_AVAILABLE = True
except ImportError:
    _RMS_ENERGY_AVAILABLE = False
    # Log warning if import fails during module load
    logging.getLogger(__name__).warning(
        "Could not import rms_energy from sygnals.core.audio.features. "
        "RMS envelope calculation via amplitude_envelope(method='rms') will fail."
    )


logger = logging.getLogger(__name__) # Get logger for this module

# --- FFT-related functions ---

def compute_fft(
    data: NDArray[np.float64],
    fs: Union[int, float] = 1.0,
    n: Optional[int] = None,
    window: Optional[str] = "hann"
) -> Tuple[NDArray[np.float64], NDArray[np.complex128]]:
    """
    Computes the Fast Fourier Transform (FFT) of a real-valued signal using scipy.fft.

    Args:
        data: Input time-domain signal (1D NumPy array of float64).
        fs: Sampling frequency of the signal (default: 1.0 Hz).
        n: Length of the FFT. If None, uses the length of the data.
           If n > len(data), the data is zero-padded.
           If n < len(data), the data is truncated.
        window: Name of the window function to apply before FFT (e.g., 'hann', 'hamming').
                Applied using scipy.signal.get_window. If None, no window is applied.

    Returns:
        A tuple containing:
        - freqs (NDArray[np.float64]): Array of frequencies corresponding to the FFT bins.
                                      Only positive frequencies up to Nyquist are typically relevant
                                      for real input signals, but the full array is returned.
        - spectrum (NDArray[np.complex128]): Complex-valued FFT result (full spectrum).

    Raises:
        ValueError: If input data is not 1D or window type is invalid.
        Exception: For other errors during FFT computation or windowing.

    Example:
        >>> import numpy as np
        >>> fs = 100
        >>> t = np.arange(fs) / fs
        >>> signal = np.sin(2 * np.pi * 10 * t) # 10 Hz sine wave
        >>> freqs, spectrum = compute_fft(signal, fs=fs)
        >>> peak_freq_index = np.argmax(np.abs(spectrum[:fs//2]))
        >>> print(f"Detected peak frequency: {freqs[peak_freq_index]:.2f} Hz")
        Detected peak frequency: 10.00 Hz
    """
    if data.ndim != 1:
        raise ValueError("Input data must be a 1D array.")

    data_processed = data # Work on a copy if windowing or padding/truncating
    if window:
        logger.debug(f"Applying '{window}' window before FFT.")
        try:
            # Apply window using the dedicated function
            data_processed = apply_window(data, window_type=window)
        except ValueError as e:
            # Re-raise ValueError for invalid window type
            raise ValueError(f"Invalid window type '{window}': {e}") from e
        except Exception as e:
             logger.warning(f"Unexpected error applying window '{window}': {e}. Proceeding without window.")
             data_processed = data # Revert to original data on unexpected error

    if n is None:
        n = data_processed.shape[0]
    elif n != data_processed.shape[0]:
         logger.debug(f"Adjusting data length from {data_processed.shape[0]} to {n} for FFT.")
         # Padding or truncation happens implicitly in fft() if n differs from data length

    logger.debug(f"Computing FFT with N={n}, Fs={fs}")
    try:
        # Use scipy.fft.fft
        spectrum = fft(data_processed, n=n)
        # Use scipy.fft.fftfreq to get frequencies
        freqs = fftfreq(n, d=1/fs)
    except Exception as e:
        logger.error(f"Error during FFT computation: {e}")
        raise

    # Ensure output types are consistent
    return freqs.astype(np.float64, copy=False), spectrum.astype(np.complex128, copy=False)

def compute_ifft(
    spectrum: NDArray[np.complex128],
    n: Optional[int] = None
) -> NDArray[np.float64]:
    """
    Computes the Inverse Fast Fourier Transform (IFFT) using scipy.fft.

    Assumes the input spectrum corresponds to a real-valued time-domain signal,
    meaning the spectrum should exhibit conjugate symmetry if it was derived
    from a real signal. Returns the real part of the result.

    Args:
        spectrum: Complex-valued frequency spectrum (complex128).
        n: Length of the inverse FFT. If None, uses the length of the spectrum.
           Should typically match the original FFT length `n` used to generate the spectrum.

    Returns:
        Real-valued time-domain signal (float64).

    Raises:
        ValueError: If input spectrum is not 1D.
        Exception: For errors during IFFT computation.

    Example:
        >>> freqs, spectrum = compute_fft(signal, fs=fs)
        >>> reconstructed_signal = compute_ifft(spectrum)
        >>> np.allclose(signal, reconstructed_signal)
        True
    """
    if spectrum.ndim != 1:
        raise ValueError("Input spectrum must be a 1D array.")
    if n is None:
        n = spectrum.shape[0]

    logger.debug(f"Computing IFFT with N={n}")
    try:
        # Use scipy.fft.ifft
        time_domain_signal = ifft(spectrum, n=n)
    except Exception as e:
        logger.error(f"Error during IFFT computation: {e}")
        raise

    # Return the real part, assuming the original signal was real
    # Small imaginary parts might exist due to numerical precision
    imag_part_max = np.max(np.abs(np.imag(time_domain_signal)))
    if imag_part_max > 1e-9: # Threshold for warning
         logger.warning(f"Significant imaginary part found in IFFT result (max abs: {imag_part_max:.2e}). "
                        "Input spectrum might not have conjugate symmetry.")
    return np.real(time_domain_signal).astype(np.float64, copy=False)


# --- Time-Frequency Transforms ---

def compute_stft(
    y: NDArray[np.float64],
    n_fft: int = 2048,
    hop_length: Optional[int] = None,
    win_length: Optional[int] = None,
    window: str = 'hann',
    center: bool = True,
    pad_mode: str = 'constant', # Default in librosa 0.10+
) -> NDArray[np.complex128]:
    """
    Computes the Short-Time Fourier Transform (STFT) using librosa.

    STFT breaks down the signal into short, overlapping frames and computes the FFT
    for each frame, providing time-localized frequency information.

    Args:
        y: Input time-domain signal (1D float64).
        n_fft: Length of the FFT window. Determines frequency resolution.
        hop_length: Number of samples between successive frames. Determines time resolution.
                    Defaults to `win_length // 4` if `win_length` is specified,
                    otherwise `n_fft // 4`.
        win_length: Each frame of audio is windowed by `window` of length `win_length`.
                    Affects spectral leakage. Defaults to `n_fft`.
        window: Window function name (see scipy.signal.get_window) or a window array.
        center: If True, pad `y` at the beginning and end so that frame `t` is centered
                at `y[t * hop_length]`. If False, frame `t` begins at `y[t * hop_length]`.
        pad_mode: Padding mode used if `center=True`. Default 'constant' pads with zeros.
                  See `numpy.pad` for other options.

    Returns:
        Complex-valued STFT matrix (shape: (1 + n_fft/2, num_frames)).
        Rows correspond to frequency bins, columns correspond to time frames.

    Raises:
        ValueError: If input data is not 1D.
        Exception: For errors during librosa STFT computation.

    Example:
        >>> sr = 22050
        >>> y = librosa.chirp(fmin=100, fmax=5000, sr=sr, duration=2)
        >>> stft_matrix = compute_stft(y, n_fft=1024, hop_length=256)
        >>> print(stft_matrix.shape)
        (513, 173) # Example shape, depends on signal length and parameters
    """
    if y.ndim != 1:
        raise ValueError("Input data must be a 1D array.")
    logger.debug(f"Computing STFT: n_fft={n_fft}, hop={hop_length}, win_len={win_length}, window={window}, center={center}")
    try:
        # Use librosa.stft
        stft_matrix = librosa.stft(
            y=y,
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=win_length,
            window=window,
            center=center,
            pad_mode=pad_mode,
        )
        # Ensure output type
        return stft_matrix.astype(np.complex128, copy=False)
    except Exception as e:
        logger.error(f"Error computing STFT: {e}")
        raise

def compute_cqt(
    y: NDArray[np.float64],
    sr: int,
    hop_length: Optional[int] = 512,
    fmin: Optional[float] = None,
    n_bins: int = 84,
    bins_per_octave: int = 12,
    **kwargs: Any # Other librosa.cqt args (tuning, filter_scale, norm, res_type etc.)
) -> NDArray[np.complex128]:
    """
    Computes the Constant-Q Transform (CQT) using librosa.

    CQT provides logarithmically spaced frequency bins, which is often useful for
    analyzing musical audio as it aligns well with musical pitch perception.

    Args:
        y: Input time-domain signal (1D float64).
        sr: Sampling rate.
        hop_length: Number of samples between successive CQT columns (time frames).
        fmin: Minimum frequency (Hz) for the lowest CQT bin. Defaults to C1 (~32.7 Hz) if None.
        n_bins: Total number of CQT frequency bins.
        bins_per_octave: Number of bins per octave. Determines frequency resolution within an octave.
        **kwargs: Additional arguments passed to `librosa.cqt` (e.g., `tuning`, `filter_scale`, `norm`, `res_type`).

    Returns:
        Complex-valued CQT matrix (shape: (n_bins, num_frames)).

    Raises:
        ValueError: If input data is not 1D.
        Exception: For errors during librosa CQT computation.

    Example:
        >>> sr = 22050
        >>> y = librosa.tone(frequency=440, sr=sr, duration=1) # A4 note
        >>> cqt_matrix = compute_cqt(y, sr=sr, n_bins=60, bins_per_octave=12)
        >>> print(cqt_matrix.shape)
        (60, 44) # Example shape
    """
    if y.ndim != 1:
        raise ValueError("Input data must be a 1D array.")
    if fmin is None:
        fmin = librosa.note_to_hz('C1') # Default to C1 if not specified
    logger.debug(f"Computing CQT: sr={sr}, hop={hop_length}, fmin={fmin:.2f}, n_bins={n_bins}, bins_per_octave={bins_per_octave}, kwargs={kwargs}")
    try:
        # Use librosa.cqt
        cqt_matrix = librosa.cqt(
            y=y,
            sr=sr,
            hop_length=hop_length,
            fmin=fmin,
            n_bins=n_bins,
            bins_per_octave=bins_per_octave,
            **kwargs
        )
        # Ensure output type
        return cqt_matrix.astype(np.complex128, copy=False)
    except Exception as e:
        logger.error(f"Error computing CQT: {e}")
        raise


# --- Convolution-related functions ---

def apply_convolution(
    data: NDArray[np.float64],
    kernel: NDArray[np.float64],
    mode: Literal['full', 'valid', 'same'] = "same"
) -> NDArray[np.float64]:
    """
    Applies 1D convolution using scipy.signal.fftconvolve (FFT-based method).

    Convolution is used for filtering, smoothing, edge detection, etc.

    Args:
        data: Input signal (1D NumPy array of float64).
        kernel: The convolution kernel (filter coefficients) (1D NumPy array of float64).
        mode: Convolution mode ('full', 'valid', 'same'). Default is 'same'.
              - 'full': Returns the full discrete linear convolution. Output size is N+M-1.
              - 'valid': Returns only parts that do not rely on zero-padding. Output size is max(N, M) - min(N, M) + 1.
              - 'same': Returns output of the same size as `data`, centered.

    Returns:
        The result of the convolution (float64).

    Raises:
        ValueError: If input data or kernel is not 1D.
        Exception: For errors during convolution.

    Example:
        >>> signal = np.array([0, 0, 1, 1, 1, 0, 0], dtype=float)
        >>> kernel = np.array([1, -1], dtype=float) # Simple difference filter
        >>> result = apply_convolution(signal, kernel, mode='same')
        >>> print(result)
        [ 0.  1.  0.  0. -1.  0.  0.]
    """
    if data.ndim != 1 or kernel.ndim != 1:
        raise ValueError("Input data and kernel must be 1D arrays.")
    logger.debug(f"Applying convolution with kernel size {kernel.shape[0]}, mode='{mode}'")
    try:
        # Use scipy.signal.fftconvolve for potentially better performance on large arrays
        result = fftconvolve(data, kernel, mode=mode)
        # Ensure output type
        return result.astype(np.float64, copy=False)
    except Exception as e:
        logger.error(f"Error during convolution: {e}")
        raise


# --- Correlation ---

def compute_correlation(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    mode: Literal['full', 'valid', 'same'] = 'full',
    method: Literal['auto', 'direct', 'fft'] = 'auto'
) -> NDArray[np.float64]:
    """
    Computes the cross-correlation of two 1-dimensional sequences using scipy.signal.correlate.

    Cross-correlation measures the similarity between two signals as a function of the
    time lag applied to one of them.

    Args:
        x: First input sequence (1D float64).
        y: Second input sequence (1D float64).
        mode: Correlation mode ('full', 'valid', 'same'). Default: 'full'.
              Determines the size of the output array based on overlap.
        method: Computation method ('auto', 'direct', 'fft'). 'auto' chooses the fastest.
                'fft' uses FFT-based correlation, 'direct' uses direct summation. Default: 'auto'.

    Returns:
        Cross-correlation result (float64). The interpretation of lags depends on the `mode`.
        For 'full', the zero lag corresponds to the center element.

    Raises:
        ValueError: If input sequences are not 1D.
        Exception: For errors during correlation computation.

    Example:
        >>> x = np.array([0, 1, 2, 1, 0], dtype=float)
        >>> y = np.array([0, 0, 1, 2, 1], dtype=float) # y is x shifted right by 1
        >>> corr = compute_correlation(x, y, mode='full')
        >>> lags = np.arange(-(len(x)-1), len(y)) # Lags for 'full' mode
        >>> peak_lag = lags[np.argmax(corr)]
        >>> print(f"Peak correlation at lag: {peak_lag}")
        Peak correlation at lag: 1
    """
    if x.ndim != 1 or y.ndim != 1:
        raise ValueError("Input sequences for correlation must be 1D arrays.")
    logger.debug(f"Computing cross-correlation: mode='{mode}', method='{method}'")
    try:
        # Use scipy.signal.correlate
        correlation = correlate(x, y, mode=mode, method=method)
        # Ensure output type
        return correlation.astype(np.float64, copy=False)
    except Exception as e:
        logger.error(f"Error computing correlation: {e}")
        raise

def compute_autocorrelation(
    x: NDArray[np.float64],
    mode: Literal['full', 'valid', 'same'] = 'full',
    method: Literal['auto', 'direct', 'fft'] = 'auto'
) -> NDArray[np.float64]:
    """
    Computes the auto-correlation of a 1-dimensional sequence using scipy.signal.correlate.

    Auto-correlation measures the similarity of a signal with a lagged version of itself.
    Useful for finding periodic patterns or estimating pitch.

    Args:
        x: Input sequence (1D float64).
        mode: Correlation mode ('full', 'valid', 'same'). Default: 'full'.
        method: Computation method ('auto', 'direct', 'fft'). Default: 'auto'.

    Returns:
        Auto-correlation result (float64). For 'full' mode, the center element
        corresponds to zero lag and typically has the maximum value.

    Raises:
        ValueError: If input sequence is not 1D.
        Exception: For errors during correlation computation.

    Example:
        >>> fs = 100
        >>> t = np.arange(fs*2) / fs
        >>> signal = np.sin(2 * np.pi * 10 * t) # 10 Hz sine wave
        >>> autocorr = compute_autocorrelation(signal, mode='full')
        >>> lags = np.arange(-(len(signal)-1), len(signal))
        >>> zero_lag_index = len(signal) - 1
        >>> assert np.argmax(autocorr) == zero_lag_index # Peak at zero lag
        >>> period_samples = fs / 10 # Expected period = 10 samples
        >>> peak_lag_index = zero_lag_index + int(round(period_samples))
        >>> # Check for secondary peak at the expected period
        >>> assert autocorr[peak_lag_index] > 0.8 * autocorr[zero_lag_index]
    """
    logger.debug(f"Computing auto-correlation: mode='{mode}', method='{method}'")
    # Autocorrelation is correlation with itself
    return compute_correlation(x, x, mode=mode, method=method)


# --- Power Spectral Density (PSD) ---

def compute_psd_periodogram(
    x: NDArray[np.float64],
    fs: float = 1.0,
    window: str = 'hann',
    nfft: Optional[int] = None,
    detrend: Union[str, bool] = 'constant',
    scaling: Literal['density', 'spectrum'] = 'density'
) -> Tuple[NDArray[np.float64], NDArray[np.float64]]:
    """
    Estimates Power Spectral Density using Periodogram method (scipy.signal.periodogram).

    The periodogram is the squared magnitude of the FFT, normalized. It provides a basic
    estimate of the power distribution across frequencies but can be noisy.

    Args:
        x: Input time series (1D float64).
        fs: Sampling frequency (Hz).
        window: Window function name or array applied to `x` before FFT. Default: 'hann'.
        nfft: Length of the FFT used. If None, defaults to len(x). Zero-padding if nfft > len(x).
        detrend: Specifies how to detrend `x` before FFT ('constant', 'linear', False). Default: 'constant'.
        scaling: 'density' returns Power Spectral Density (units V**2/Hz).
                 'spectrum' returns Power Spectrum (units V**2). Default: 'density'.

    Returns:
        Tuple containing:
        - frequencies (NDArray[np.float64]): Frequencies of the PSD estimate (one-sided).
        - Pxx (NDArray[np.float64]): Power Spectral Density or Power Spectrum estimate.

    Raises:
        ValueError: If input data is not 1D.
        Exception: For errors during periodogram calculation.

    Example:
        >>> fs = 1000
        >>> t = np.arange(fs) / fs
        >>> signal = np.sin(2 * np.pi * 100 * t) # 100 Hz sine wave
        >>> freqs, psd = compute_psd_periodogram(signal, fs=fs)
        >>> peak_freq_index = np.argmax(psd)
        >>> print(f"Peak frequency in PSD: {freqs[peak_freq_index]:.2f} Hz")
        Peak frequency in PSD: 100.00 Hz
    """
    if x.ndim != 1:
        raise ValueError("Input data must be a 1D array.")
    logger.debug(f"Computing PSD (Periodogram): fs={fs}, window={window}, nfft={nfft}, detrend={detrend}, scaling={scaling}")
    try:
        # Use scipy.signal.periodogram
        frequencies, Pxx = periodogram(
            x,
            fs=fs,
            window=window,
            nfft=nfft,
            detrend=detrend,
            return_onesided=True, # Typically want one-sided for real signals
            scaling=scaling
        )
        # Ensure output types
        return frequencies.astype(np.float64, copy=False), Pxx.astype(np.float64, copy=False)
    except Exception as e:
        logger.error(f"Error computing Periodogram PSD: {e}")
        raise

def compute_psd_welch(
    x: NDArray[np.float64],
    fs: float = 1.0,
    window: str = 'hann',
    nperseg: Optional[int] = None,
    noverlap: Optional[int] = None,
    nfft: Optional[int] = None,
    detrend: Union[str, bool] = 'constant',
    scaling: Literal['density', 'spectrum'] = 'density'
) -> Tuple[NDArray[np.float64], NDArray[np.float64]]:
    """
    Estimates Power Spectral Density using Welch's method (scipy.signal.welch).

    Welch's method improves upon the periodogram by averaging the periodograms
    of overlapping segments of the signal, reducing noise/variance.

    Args:
        x: Input time series (1D float64).
        fs: Sampling frequency (Hz).
        window: Window function name or array applied to each segment. Default: 'hann'.
        nperseg: Length of each segment. Defaults to 256 if None. Controls frequency resolution.
        noverlap: Number of points to overlap between segments. Defaults to nperseg // 2 if None.
                  Higher overlap reduces variance but increases computation.
        nfft: Length of the FFT used for each segment. Defaults to nperseg.
        detrend: Specifies how to detrend each segment ('constant', 'linear', False). Default: 'constant'.
        scaling: 'density' (V**2/Hz) or 'spectrum' (V**2). Default: 'density'.

    Returns:
        Tuple containing:
        - frequencies (NDArray[np.float64]): Frequencies of the PSD estimate (one-sided).
        - Pxx (NDArray[np.float64]): Power Spectral Density or Power Spectrum estimate.

    Raises:
        ValueError: If input data is not 1D.
        Exception: For errors during Welch calculation.

    Example:
        >>> fs = 1000
        >>> t = np.arange(fs) / fs
        >>> signal = np.sin(2 * np.pi * 100 * t) + 0.1 * np.random.randn(fs) # Sine + noise
        >>> freqs, psd = compute_psd_welch(signal, fs=fs, nperseg=256)
        >>> peak_freq_index = np.argmax(psd)
        >>> print(f"Peak frequency in Welch PSD: {freqs[peak_freq_index]:.2f} Hz")
        Peak frequency in Welch PSD: 100.00 Hz
    """
    if x.ndim != 1:
        raise ValueError("Input data must be a 1D array.")
    logger.debug(f"Computing PSD (Welch): fs={fs}, window={window}, nperseg={nperseg}, noverlap={noverlap}, nfft={nfft}, detrend={detrend}, scaling={scaling}")
    try:
        # Use scipy.signal.welch
        frequencies, Pxx = welch(
            x,
            fs=fs,
            window=window,
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
            detrend=detrend,
            return_onesided=True, # Typically want one-sided for real signals
            scaling=scaling
        )
        # Ensure output types
        return frequencies.astype(np.float64, copy=False), Pxx.astype(np.float64, copy=False)
    except Exception as e:
        logger.error(f"Error computing Welch PSD: {e}")
        raise


# --- Envelope Detection ---

def amplitude_envelope(
    y: NDArray[np.float64],
    method: Literal['hilbert', 'rms'] = 'hilbert',
    frame_length: Optional[int] = None, # Required for RMS method
    hop_length: Optional[int] = None    # Required for RMS method
) -> NDArray[np.float64]:
    """
    Computes the amplitude envelope of a signal.

    The envelope represents the slower variations in the signal's amplitude.

    Args:
        y: Input time series (1D float64).
        method: 'hilbert' uses the magnitude of the analytic signal (computed via
                Hilbert transform). Provides instantaneous amplitude.
                'rms' uses frame-based Root Mean Square energy. Provides a smoothed
                envelope based on local energy. Requires `rms_energy` to be available.
        frame_length: Frame length in samples (required for 'rms' method).
        hop_length: Hop length in samples (required for 'rms' method).

    Returns:
        Amplitude envelope (1D float64).
        - For 'hilbert', length matches input `y`.
        - For 'rms', length corresponds to the number of frames.

    Raises:
        ValueError: If input data is not 1D, method is invalid, or required parameters for 'rms' are missing.
        ImportError: If 'rms' method is chosen but `rms_energy` could not be imported.
        Exception: For errors during Hilbert or RMS calculation.

    Example:
        >>> sr = 1000
        >>> t = np.arange(sr) / sr
        >>> signal = np.sin(2 * np.pi * 10 * t) * np.exp(-t * 5) # Decaying sine
        >>> env_hilbert = amplitude_envelope(signal, method='hilbert')
        >>> env_rms = amplitude_envelope(signal, method='rms', frame_length=128, hop_length=64)
        >>> # env_hilbert will closely follow the exp(-t*5) decay
        >>> # env_rms will be a smoothed, frame-based version of the decay
    """
    if y.ndim != 1:
        raise ValueError("Input data must be a 1D array.")
    logger.debug(f"Computing Amplitude Envelope using method: {method}")

    if method == 'hilbert':
        try:
            # Hilbert transform gives the analytic signal: y + j*hilbert(y)
            # The magnitude |y + j*hilbert(y)| is the instantaneous amplitude (envelope)
            analytic_signal = hilbert(y)
            envelope = np.abs(analytic_signal)
            return envelope.astype(np.float64, copy=False)
        except Exception as e:
            logger.error(f"Error computing Hilbert envelope: {e}")
            raise
    elif method == 'rms':
        if not _RMS_ENERGY_AVAILABLE:
             # Raise error if rms_energy couldn't be imported
             raise ImportError("rms_energy function not found (check sygnals.core.audio.features). "
                               "Cannot compute RMS envelope.")
        if frame_length is None or hop_length is None:
            raise ValueError("frame_length and hop_length are required for 'rms' envelope method.")
        try:
            # Use the imported RMS energy function
            # Note: RMS is related to envelope but not exactly the same as Hilbert envelope.
            # It gives energy per frame, acting as a smoothed, frame-based envelope.
            # Assume center=True for consistency with other librosa features
            rms_env = rms_energy(y, frame_length=frame_length, hop_length=hop_length, center=True)
            return rms_env # Already float64
        except Exception as e:
            logger.error(f"Error computing RMS envelope: {e}")
            raise
    else:
        raise ValueError(f"Unsupported envelope method: {method}. Choose 'hilbert' or 'rms'.")


# --- Window functions ---

def apply_window(
    data: NDArray[np.float64],
    window_type: str = "hann"
) -> NDArray[np.float64]:
    """
    Applies a specified window function to the data using scipy.signal.get_window.

    Windowing is often applied before FFT to reduce spectral leakage.

    Args:
        data: Input signal (1D NumPy array of float64).
        window_type: Name of the window function (e.g., 'hann', 'hamming', 'blackman', 'bartlett').
                     See `scipy.signal.get_window` documentation for available types.

    Returns:
        Windowed data (float64).

    Raises:
        ValueError: If input data is not 1D or window_type is invalid.
        Exception: For other errors during window generation or application.

    Example:
        >>> signal = np.ones(10)
        >>> windowed_signal = apply_window(signal, window_type='hann')
        >>> print(windowed_signal.shape)
        (10,)
        >>> print(windowed_signal[0], windowed_signal[-1]) # Hann window goes to zero at ends
        0.0 0.0
    """
    if data.ndim != 1:
        raise ValueError("Input data must be a 1D array.")
    logger.debug(f"Applying '{window_type}' window.")
    try:
        # Get the window function values
        # fftbins=False ensures the window is symmetric and suitable for general signal processing
        window = get_window(window_type, data.shape[0], fftbins=False)
        # Ensure window length matches data length precisely (should match if fftbins=False)
        if len(window) != len(data):
             # This case should be rare with fftbins=False but handle defensively
             raise ValueError(f"Internal error: Window length mismatch after get_window "
                              f"(got {len(window)}, expected {len(data)}).")
        # Apply window by element-wise multiplication
        return (data * window).astype(np.float64, copy=False)
    except ValueError as e:
        # Specific error for invalid window type from get_window
        logger.error(f"Invalid window type '{window_type}': {e}")
        raise ValueError(f"Invalid window type '{window_type}'.") from e
    except Exception as e:
        # Catch other potential errors during window application
        logger.error(f"Unexpected error applying window '{window_type}': {e}")
        raise

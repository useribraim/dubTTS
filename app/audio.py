import os
import wave
import contextlib
import subprocess
from dataclasses import dataclass
from typing import List, Iterable, Tuple, Iterator

import webrtcvad


@dataclass
class AudioSegment:
    index: int
    start_ms: int
    end_ms: int
    path: str


def convert_to_wav_16k_mono(input_path: str, out_path: str) -> str:
    """
    Converts any audio/video file to 16kHz mono 16-bit PCM WAV using ffmpeg.
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        out_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


def _read_wav_pcm16(wav_path: str) -> Tuple[bytes, int]:
    with contextlib.closing(wave.open(wav_path, "rb")) as wf:
        if wf.getnchannels() != 1:
            raise ValueError("WAV must be mono")
        if wf.getsampwidth() != 2:
            raise ValueError("WAV must be 16-bit PCM")
        sample_rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
        return pcm, sample_rate


def _frame_generator(pcm: bytes, sample_rate: int, frame_ms: int) -> Iterable[bytes]:
    bytes_per_sample = 2
    frame_size = int(sample_rate * (frame_ms / 1000.0)) * bytes_per_sample
    for i in range(0, len(pcm), frame_size):
        frame = pcm[i : i + frame_size]
        if len(frame) == frame_size:
            yield frame


def _pcm_slice_to_wav(pcm: bytes, sample_rate: int, out_path: str) -> None:
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def match_audio_duration(input_wav: str, target_duration_ms: int, output_wav: str) -> None:
    """
    Adjusts audio duration to match target duration.
    If input is shorter: time-stretches using atempo filter
    If input is longer: trims to target duration
    """
    import contextlib
    
    # Get current duration
    with contextlib.closing(wave.open(input_wav, "rb")) as wf:
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        current_duration_ms = (n_frames / sample_rate) * 1000.0
    
    os.makedirs(os.path.dirname(output_wav), exist_ok=True)
    
    # If durations are very close (within 50ms), just copy
    if abs(current_duration_ms - target_duration_ms) < 50:
        cmd = ["ffmpeg", "-y", "-i", input_wav, "-c", "copy", output_wav]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    
    target_duration_sec = target_duration_ms / 1000.0
    
    if current_duration_ms < target_duration_ms:
        # Time-stretch: use atempo filter (range: 0.5 to 2.0)
        # Calculate how much we need to slow down (stretch factor)
        # atempo=0.5 means play at half speed (doubles duration)
        stretch_ratio = target_duration_ms / current_duration_ms
        
        # Build filter chain dynamically
        filters = []
        remaining_ratio = stretch_ratio
        
        # Keep applying 0.5x filters (each doubles duration) until we're close
        while remaining_ratio > 2.0:
            filters.append("atempo=0.5")
            remaining_ratio /= 2.0  # Each 0.5x filter doubles the duration
        
        # Add final filter to get exact duration
        # remaining_ratio is now between 1.0 and 2.0
        if remaining_ratio > 1.01:  # Only add if significantly different
            # atempo value = 1/remaining_ratio (to slow down by remaining_ratio)
            final_tempo = 1.0 / remaining_ratio
            # Clamp to valid range [0.5, 2.0]
            final_tempo = max(0.5, min(2.0, final_tempo))
            filters.append(f"atempo={final_tempo}")
        
        filter_chain = ",".join(filters) if filters else None
        
        if filter_chain:
            cmd = [
                "ffmpeg", "-y", "-i", input_wav,
                "-filter:a", filter_chain,
                output_wav
            ]
        else:
            # No stretching needed (shouldn't happen, but handle it)
            cmd = ["ffmpeg", "-y", "-i", input_wav, "-c", "copy", output_wav]
    else:
        # Trim to target duration
        cmd = [
            "ffmpeg", "-y", "-i", input_wav,
            "-t", str(target_duration_sec),
            "-c", "copy",
            output_wav
        ]
    
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def vad_segment_wav(
    wav_path: str,
    out_dir: str,
    aggressiveness: int = 2,
    frame_ms: int = 30,
    padding_ms: int = 300,
    min_segment_ms: int = 700,
    max_segment_ms: int = 12000,
) -> List[AudioSegment]:
    """
    Splits WAV into speech segments using WebRTC VAD.
    Returns paths to segment WAV files + timestamps.
    """
    os.makedirs(out_dir, exist_ok=True)

    pcm, sr = _read_wav_pcm16(wav_path)
    if sr not in (8000, 16000, 32000, 48000):
        raise ValueError(f"Unsupported sample rate for VAD: {sr}")

    vad = webrtcvad.Vad(aggressiveness)

    frames = list(_frame_generator(pcm, sr, frame_ms))
    if not frames:
        return []

    # Mark voiced frames
    voiced = [vad.is_speech(f, sr) for f in frames]

    # Collect segments with padding (simple state machine)
    pad_frames = int(padding_ms / frame_ms)
    segments: List[Tuple[int, int]] = []

    in_speech = False
    start = 0

    for i, is_voiced in enumerate(voiced):
        if not in_speech:
            # Start when we see voiced frames (with some look-behind padding)
            if is_voiced:
                start = max(0, i - pad_frames)
                in_speech = True
        else:
            # End when we see a run of unvoiced frames
            end_condition = True
            for j in range(i, min(i + pad_frames, len(voiced))):
                if voiced[j]:
                    end_condition = False
                    break
            if end_condition:
                end = min(len(frames), i + pad_frames)
                segments.append((start, end))
                in_speech = False

    if in_speech:
        segments.append((start, len(frames)))

    # Merge / clamp / enforce min/max durations
    bytes_per_frame = len(frames[0])
    ms_per_frame = frame_ms

    cleaned: List[Tuple[int, int]] = []
    for s, e in segments:
        dur_ms = (e - s) * ms_per_frame
        if dur_ms < min_segment_ms:
            continue

        # Split long segments
        if dur_ms > max_segment_ms:
            max_frames = int(max_segment_ms / ms_per_frame)
            k = s
            while k < e:
                cleaned.append((k, min(e, k + max_frames)))
                k += max_frames
        else:
            cleaned.append((s, e))

    # Write segment wavs
    out: List[AudioSegment] = []
    for idx, (s, e) in enumerate(cleaned):
        start_ms = s * ms_per_frame
        end_ms = e * ms_per_frame

        seg_pcm = pcm[s * bytes_per_frame : e * bytes_per_frame]
        seg_path = os.path.join(out_dir, f"seg_{idx:04d}.wav")
        _pcm_slice_to_wav(seg_pcm, sr, seg_path)

        out.append(AudioSegment(index=idx, start_ms=start_ms, end_ms=end_ms, path=seg_path))

    return out


def vad_segment_wav_stream(
    wav_path: str,
    out_dir: str,
    aggressiveness: int = 2,
    frame_ms: int = 30,
    padding_ms: int = 300,
    min_segment_ms: int = 700,
    max_segment_ms: int = 12000,
) -> Iterator[AudioSegment]:
    """
    Streams speech segments as soon as they close (end detected).
    This improves time-to-first-segment vs returning a full list.
    """
    os.makedirs(out_dir, exist_ok=True)

    with contextlib.closing(wave.open(wav_path, "rb")) as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("WAV must be mono 16-bit PCM")
        sr = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())

    if sr not in (8000, 16000, 32000, 48000):
        raise ValueError(f"Unsupported sample rate for VAD: {sr}")

    vad = webrtcvad.Vad(aggressiveness)

    bytes_per_sample = 2
    frame_size = int(sr * (frame_ms / 1000.0)) * bytes_per_sample
    if frame_size <= 0:
        return

    frames = []
    for i in range(0, len(pcm), frame_size):
        f = pcm[i : i + frame_size]
        if len(f) == frame_size:
            frames.append(f)

    pad_frames = int(padding_ms / frame_ms)
    ms_per_frame = frame_ms
    bytes_per_frame = frame_size

    in_speech = False
    start = 0
    seg_index = 0

    def write_segment(s: int, e: int) -> str:
        out_path = os.path.join(out_dir, f"seg_{seg_index:04d}.wav")
        seg_pcm = pcm[s * bytes_per_frame : e * bytes_per_frame]
        with wave.open(out_path, "wb") as wf2:
            wf2.setnchannels(1)
            wf2.setsampwidth(2)
            wf2.setframerate(sr)
            wf2.writeframes(seg_pcm)
        return out_path

    i = 0
    while i < len(frames):
        is_voiced = vad.is_speech(frames[i], sr)

        if not in_speech:
            if is_voiced:
                start = max(0, i - pad_frames)
                in_speech = True
        else:
            # end when next pad window is all unvoiced
            end_condition = True
            for j in range(i, min(i + pad_frames, len(frames))):
                if vad.is_speech(frames[j], sr):
                    end_condition = False
                    break

            if end_condition:
                end = min(len(frames), i + pad_frames)

                dur_ms = (end - start) * ms_per_frame
                if dur_ms >= min_segment_ms:
                    # split if too long
                    max_frames = int(max_segment_ms / ms_per_frame)
                    k = start
                    while k < end:
                        e2 = min(end, k + max_frames)
                        seg_path = write_segment(k, e2)

                        yield AudioSegment(
                            index=seg_index,
                            start_ms=k * ms_per_frame,
                            end_ms=e2 * ms_per_frame,
                            path=seg_path,
                        )
                        seg_index += 1
                        k = e2

                in_speech = False

        i += 1

    # tail
    if in_speech:
        end = len(frames)
        dur_ms = (end - start) * ms_per_frame
        if dur_ms >= min_segment_ms:
            max_frames = int(max_segment_ms / ms_per_frame)
            k = start
            while k < end:
                e2 = min(end, k + max_frames)
                seg_path = write_segment(k, e2)
                yield AudioSegment(seg_index, k * ms_per_frame, e2 * ms_per_frame, seg_path)
                seg_index += 1
                k = e2


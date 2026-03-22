"""Dual-stream audio capture — mic + system audio to WAV files.

Uses sounddevice InputStreams with a queue-based writer thread
so meetings of any length can be recorded without buffering in memory.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from scribe.config import AudioConfig

logger = logging.getLogger(__name__)

# Sentinel to signal writer thread to stop
_STOP = None


class StreamWriter:
    """Drains audio chunks from a queue to a WAV file on disk."""

    def __init__(self, path: Path, sample_rate: int, channels: int):
        self.path = path
        self.sr = sample_rate
        self.channels = channels
        self.queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=200)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._write_loop, daemon=True)
        self._thread.start()

    def _write_loop(self) -> None:
        with sf.SoundFile(
            str(self.path),
            mode="w",
            samplerate=self.sr,
            channels=self.channels,
            format="WAV",
            subtype="PCM_16",
        ) as f:
            while True:
                chunk = self.queue.get()
                if chunk is _STOP:
                    break
                f.write(chunk)

    def put(self, data: np.ndarray) -> None:
        try:
            self.queue.put_nowait(data)
        except queue.Full:
            logger.warning(f"Writer queue full for {self.path.name}, dropping chunk")

    def stop(self) -> None:
        self.queue.put(_STOP)
        if self._thread:
            self._thread.join(timeout=5.0)


class DualRecorder:
    """Records from mic and system audio devices simultaneously."""

    def __init__(self, config: AudioConfig, output_dir: Path):
        self.config = config
        self.output_dir = output_dir
        self._mic_writer: StreamWriter | None = None
        self._sys_writer: StreamWriter | None = None
        self._mic_stream: sd.InputStream | None = None
        self._sys_stream: sd.InputStream | None = None
        self._running = False

    def start(self) -> None:
        """Open both audio streams and start writing to disk."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        sr = self.config.sample_rate
        ch = self.config.channels

        # Mic stream
        self._mic_writer = StreamWriter(self.output_dir / "mic.wav", sr, ch)
        self._mic_writer.start()

        self._mic_stream = sd.InputStream(
            samplerate=sr,
            channels=ch,
            dtype="float32",
            device=self.config.mic_device,
            blocksize=1024,
            callback=self._mic_callback,
        )

        # System audio stream
        self._sys_writer = StreamWriter(self.output_dir / "system.wav", sr, ch)
        self._sys_writer.start()

        self._sys_stream = sd.InputStream(
            samplerate=sr,
            channels=ch,
            dtype="float32",
            device=self.config.system_device,
            blocksize=1024,
            callback=self._sys_callback,
        )

        self._mic_stream.start()
        self._sys_stream.start()
        self._running = True

        mic_name = self.config.mic_device or "system default"
        sys_name = self.config.system_device or "system default"
        logger.info(f"Recording started: mic={mic_name}, system={sys_name}")

    def _mic_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning(f"Mic stream: {status}")
        if self._mic_writer:
            self._mic_writer.put(indata.copy())

    def _sys_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning(f"System stream: {status}")
        if self._sys_writer:
            self._sys_writer.put(indata.copy())

    def stop(self) -> None:
        """Stop recording and close all streams."""
        self._running = False

        for stream in (self._mic_stream, self._sys_stream):
            if stream:
                stream.stop()
                stream.close()

        for writer in (self._mic_writer, self._sys_writer):
            if writer:
                writer.stop()

        self._mic_stream = None
        self._sys_stream = None
        logger.info(f"Recording saved to {self.output_dir}")

    @property
    def is_running(self) -> bool:
        return self._running

from __future__ import annotations

import socket
import struct
import sys
import time
from pathlib import Path

SCRCPY_VERSION = "2.4"
SERVER_SHA256 = "93c272b7438605c055e127f7444064ed78fa9ca49f81156777fd201e79ce7ba3"
REMOTE_SERVER = "/data/local/tmp/damai-scrcpy-server-v2.4.jar"


class ScrcpyError(RuntimeError):
    pass


def default_server_path() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "resources" / f"scrcpy-server-v{SCRCPY_VERSION}"
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parent / "resources" / f"scrcpy-server-v{SCRCPY_VERSION}"


class ScrcpyStream:
    def __init__(
        self,
        serial: str,
        *,
        server_path: Path | None = None,
        # 保持原生分辨率和码率以稳定模板匹配；30fps 把开售瞬间的帧间隔从
        # 66ms 压到 33ms，代价是编码/解码 CPU 翻倍
        max_size: int = 1600,
        max_fps: int = 30,
        bitrate: int = 8_000_000,
        startup_timeout: float = 5.0,
    ):
        self.serial = serial
        self.server_path = server_path or default_server_path()
        self.max_size = max_size
        self.max_fps = max_fps
        self.bitrate = bitrate
        self.startup_timeout = startup_timeout
        self.resolution = None
        self.device_name = ""
        self._device = None
        self._server = None
        self._video = None
        self._control = None
        self._codec = None
        self._alive = False

    def start(self) -> None:
        if self._alive:
            return
        if not self.server_path.is_file():
            raise ScrcpyError(f"未找到 Scrcpy server：{self.server_path}")
        try:
            from adbutils import Network, adb
            from av.codec import CodecContext
        except ImportError as exc:
            raise ScrcpyError(f"Scrcpy 运行依赖缺失：{exc}") from exc

        self._device = adb.device(serial=self.serial)
        self._codec = CodecContext.create("h264", "r")
        try:
            self._device.sync.push(str(self.server_path), REMOTE_SERVER)
            self._server = self._device.shell(
                [
                    f"CLASSPATH={REMOTE_SERVER}",
                    "app_process",
                    "/",
                    "com.genymobile.scrcpy.Server",
                    SCRCPY_VERSION,
                    "log_level=warn",
                    f"max_size={self.max_size}",
                    f"max_fps={self.max_fps}",
                    f"video_bit_rate={self.bitrate}",
                    "tunnel_forward=true",
                    "send_frame_meta=false",
                    "control=true",
                    "audio=false",
                    "show_touches=false",
                    "stay_awake=true",
                    "power_off_on_close=false",
                    "clipboard_autosync=false",
                ],
                stream=True,
            )
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                try:
                    self._video = self._device.create_connection(
                        Network.LOCAL_ABSTRACT, "scrcpy"
                    )
                    break
                except Exception:
                    time.sleep(0.05)
            if self._video is None:
                raise ScrcpyError("Scrcpy server 启动超时")
            self._video.settimeout(self.startup_timeout)
            if self._recv_exact(1) != b"\x00":
                raise ScrcpyError("Scrcpy 握手失败")
            self._control = self._device.create_connection(
                Network.LOCAL_ABSTRACT, "scrcpy"
            )
            raw_name = self._recv_exact(64)
            self.device_name = raw_name.decode("utf-8", errors="replace").rstrip("\x00")
            self.resolution = struct.unpack(">HH", self._recv_exact(4))
            self._video.setblocking(False)
            self._alive = True
        except ScrcpyError:
            self.stop()
            raise
        except Exception as exc:
            self.stop()
            raise ScrcpyError(f"Scrcpy 启动失败：{type(exc).__name__}: {exc}") from exc

    def _recv_exact(self, size: int) -> bytes:
        chunks = []
        left = size
        deadline = time.monotonic() + self.startup_timeout
        while left:
            if time.monotonic() >= deadline:
                raise ScrcpyError("Scrcpy 握手读取超时")
            try:
                chunk = self._video.recv(left)
            except (BlockingIOError, socket.timeout):
                time.sleep(0.01)
                continue
            if not chunk:
                raise ScrcpyError("Scrcpy 连接已断开")
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    def read_latest(self):
        if not self._alive:
            return None
        from av.error import InvalidDataError

        latest = None
        while True:
            try:
                raw = self._video.recv(0x40000)
            except (BlockingIOError, socket.timeout):
                break
            except InvalidDataError:
                continue
            if not raw:
                self.stop()
                raise ScrcpyError("Scrcpy 视频流已断开")
            try:
                packets = self._codec.parse(raw)
                for packet in packets:
                    for frame in self._codec.decode(packet):
                        latest = frame.to_ndarray(format="bgr24")
            except InvalidDataError:
                continue
        if latest is not None:
            self.resolution = (latest.shape[1], latest.shape[0])
        return latest

    def stop(self) -> None:
        self._alive = False
        for resource in (self._control, self._video, self._server):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass
        self._control = None
        self._video = None
        self._server = None

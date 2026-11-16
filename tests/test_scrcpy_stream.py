import hashlib

import numpy as np

from scrcpy_stream import SERVER_SHA256, ScrcpyStream, default_server_path


def test_bundled_server_matches_official_checksum():
    path = default_server_path()
    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SERVER_SHA256


def test_read_latest_drains_socket_and_returns_newest_frame():
    class FakeFrame:
        def __init__(self, value):
            self.value = value

        def to_ndarray(self, format):
            assert format == "bgr24"
            return np.full((2, 3, 3), self.value, dtype=np.uint8)

    class FakeCodec:
        def parse(self, raw):
            return [raw]

        def decode(self, packet):
            return [FakeFrame(packet[0])]

    class FakeSocket:
        def __init__(self):
            self.chunks = [b"\x01", b"\x02", BlockingIOError()]

        def recv(self, _size):
            item = self.chunks.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    stream = ScrcpyStream("fake")
    stream._alive = True
    stream._codec = FakeCodec()
    stream._video = FakeSocket()

    frame = stream.read_latest()

    assert int(frame[0, 0, 0]) == 2
    assert stream.resolution == (3, 2)


def test_stop_closes_all_resources():
    class Closable:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    stream = ScrcpyStream("fake")
    resources = [Closable(), Closable(), Closable()]
    stream._control, stream._video, stream._server = resources
    stream._alive = True

    stream.stop()

    assert all(resource.closed for resource in resources)
    assert stream._alive is False
    assert stream._control is stream._video is stream._server is None

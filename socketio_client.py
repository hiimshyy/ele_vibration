#!/usr/bin/env python3
"""Tầng Socket.IO dùng chung (stdlib) cho iot_core.

Gồm:
  * WebSocket      — client RFC 6455 tối giản (text/close/ping), TLS tùy chọn.
  * parse_socketio — tách một gói Socket.IO v5.
  * SocketIOClient — nối Engine.IO v4 / Socket.IO v5, subscribe nhiều thiết bị,
                     gộp delta theo sn, gọi callback on_data(sn, level, merged).

Dùng lại bởi es_vs_01_ws.py (nhận rung) và elevator_session_logger.py
(cắt rung theo phiên thang). Chỉ thư viện chuẩn, không cần cài gì.
"""

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from urllib.parse import urlsplit

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(Exception):
    pass


class WebSocket:
    """Client WebSocket trên một TCP/TLS socket. Chỉ hỗ trợ khung text/close/ping."""

    OP_CONT = 0x0
    OP_TEXT = 0x1
    OP_BIN = 0x2
    OP_CLOSE = 0x8
    OP_PING = 0x9
    OP_PONG = 0xA

    def __init__(self, url: str, insecure: bool = False, timeout: float = 30.0):
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme in ("https", "wss"):
            self.use_tls = True
            default_port = 443
        elif scheme in ("http", "ws"):
            self.use_tls = False
            default_port = 80
        else:
            raise WebSocketError(f"Scheme không hỗ trợ: {parts.scheme}")

        self.host = parts.hostname
        self.port = parts.port or default_port
        self.resource = parts.path or "/"
        if parts.query:
            self.resource += "?" + parts.query
        self.insecure = insecure
        self.timeout = timeout
        self.sock = None
        self._buf = b""

    def connect(self) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        if self.use_tls:
            if self.insecure:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            else:
                ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        raw.settimeout(self.timeout)
        self.sock = raw
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        host_hdr = self.host if self.port in (80, 443) else f"{self.host}:{self.port}"
        request = (
            f"GET {self.resource} HTTP/1.1\r\n"
            f"Host: {host_hdr}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: es-vs-01-ws/1.0\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode())

        header = self._read_until(b"\r\n\r\n")
        text = header.decode("latin-1")
        status_line = text.split("\r\n", 1)[0]
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or parts[1] != "101":
            raise WebSocketError(f"Handshake thất bại: {status_line}")

        accept = ""
        for line in text.split("\r\n")[1:]:
            if ":" in line:
                name, _, value = line.partition(":")
                if name.strip().lower() == "sec-websocket-accept":
                    accept = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()
        ).decode()
        if accept != expected:
            raise WebSocketError("Sec-WebSocket-Accept không khớp")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketError("Kết nối đóng khi đang đọc header")
            self._buf += chunk
        idx = self._buf.index(marker) + len(marker)
        out, self._buf = self._buf[:idx], self._buf[idx:]
        return out

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("Kết nối đóng khi đang đọc khung")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_frame(self):
        b0, b1 = self._read_exact(2)
        fin = b0 & 0x80
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv_text(self):
        """Trả về chuỗi text của message kế tiếp, hoặc None nếu server đóng.
        Tự xử lý ping -> pong, close, và nối các khung bị phân mảnh."""
        data = b""
        opcode_msg = None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == self.OP_CLOSE:
                self._send_frame(self.OP_CLOSE, payload[:2])
                return None
            if opcode == self.OP_PING:
                self._send_frame(self.OP_PONG, payload)
                continue
            if opcode == self.OP_PONG:
                continue
            if opcode in (self.OP_TEXT, self.OP_BIN):
                opcode_msg = opcode
                data = payload
            elif opcode == self.OP_CONT:
                data += payload
            if fin:
                if opcode_msg == self.OP_BIN:
                    return data
                return data.decode("utf-8", "replace")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(self.OP_TEXT, text.encode("utf-8"))

    def close(self) -> None:
        if self.sock is not None:
            try:
                self._send_frame(self.OP_CLOSE, b"\x03\xe8")  # 1000 normal
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def parse_socketio(text: str):
    """Tách một gói Socket.IO (phần sau ký tự Engine.IO '4').
    Trả về (sio_type, namespace, ack_id, data)."""
    sio_type = text[0]
    rest = text[1:]
    namespace = "/"
    if rest.startswith("/"):
        comma = rest.find(",")
        if comma == -1:
            return sio_type, rest, None, None
        namespace = rest[:comma]
        rest = rest[comma + 1:]
    i = 0
    while i < len(rest) and rest[i].isdigit():
        i += 1
    ack = rest[:i] or None
    payload = rest[i:]
    data = json.loads(payload) if payload else None
    return sio_type, namespace, ack, data


class SocketIOClient:
    """Engine.IO v4 / Socket.IO v5 trên WebSocket, hỗ trợ subscribe nhiều thiết bị.

    subscriptions: danh sách dict {"sn":..., "level":...}.
    on_data(sn, level, merged): gọi mỗi khi có CONN_DATA; `merged` là state thô
        đã gộp delta của sn đó (dict dùng chung, không nên giữ tham chiếu lâu dài).
    on_connect(): sau khi Socket.IO CONNECT thành công và đã gửi subscribe.
    on_disconnect(exc): khi một kết nối rớt (trước khi thử lại).
    """

    def __init__(self, url, subscriptions, on_data, path="/socket.io/",
                 insecure=False, timeout=30.0, on_connect=None, on_disconnect=None,
                 event="CONN_DATA"):
        self.url = url
        self.subs = subscriptions
        self.on_data = on_data
        self.path = path
        self.insecure = insecure
        self.timeout = timeout
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.event = event
        self._stop = False
        self._ws = None
        self._state = {}  # sn -> dict thô đã gộp

    def _ws_url(self) -> str:
        parts = urlsplit(self.url)
        scheme = "wss" if parts.scheme in ("https", "wss") else "ws"
        host = parts.netloc or parts.path
        path = self.path.rstrip("/") + "/"
        return f"{scheme}://{host}{path}?EIO=4&transport=websocket"

    def stop(self) -> None:
        self._stop = True
        if self._ws is not None:
            self._ws.close()

    def run_once(self) -> None:
        ws = WebSocket(self._ws_url(), insecure=self.insecure, timeout=self.timeout)
        ws.connect()
        self._ws = ws
        self._state = {}
        try:
            while not self._stop:
                text = ws.recv_text()
                if text is None:
                    raise WebSocketError("Server đóng kết nối")
                if not text:
                    continue
                etype = text[0]
                body = text[1:]

                if etype == "0":  # Engine.IO OPEN
                    try:
                        hs = json.loads(body)
                        ws.sock.settimeout(
                            (hs.get("pingInterval", 25000)
                             + hs.get("pingTimeout", 20000)) / 1000.0 + 5.0)
                    except (ValueError, OSError):
                        pass
                    ws.send_text("40")
                elif etype == "2":  # PING
                    ws.send_text("3")
                elif etype == "1":  # engine CLOSE
                    raise WebSocketError("Server gửi lệnh đóng (Engine.IO close)")
                elif etype == "4":  # MESSAGE -> Socket.IO
                    sio_type, _ns, _ack, data = parse_socketio(body)
                    if sio_type == "0":  # CONNECT ok
                        for s in self.subs:
                            self._emit(ws, "CONN_SUBSCRIBE",
                                       {"sn": s["sn"], "level": s["level"]})
                        if self.on_connect:
                            self.on_connect()
                    elif sio_type == "4":
                        raise WebSocketError(f"Socket.IO CONNECT_ERROR: {data}")
                    elif sio_type == "2":  # EVENT
                        if isinstance(data, list) and data and data[0] == self.event \
                                and len(data) > 1:
                            self._dispatch(data[1])
        finally:
            ws.close()
            self._ws = None

    def _emit(self, ws, name, payload) -> None:
        ws.send_text("42" + json.dumps([name, payload], separators=(",", ":")))

    def _dispatch(self, packet) -> None:
        if not isinstance(packet, dict):
            return
        sn = packet.get("sn")
        level = packet.get("level")
        data = packet.get("data")
        if not isinstance(data, dict):
            return
        merged = self._state.setdefault(sn, {})
        merged.update(data)
        self.on_data(sn, level, merged)

    def run_forever(self, reconnect: float = 3.0) -> None:
        while not self._stop:
            try:
                self.run_once()
            except (WebSocketError, OSError, ValueError) as exc:
                if self.on_disconnect:
                    self.on_disconnect(exc)
                if self._stop or reconnect <= 0:
                    break
                time.sleep(reconnect)
                continue
            break

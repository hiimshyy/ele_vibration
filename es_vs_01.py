#!/usr/bin/env python3
"""Đọc cảm biến rung ES-VS-01 (Modbus RTU, RS485) qua bộ chuyển USB-RS485.

Chỉ dùng thư viện chuẩn (termios), không cần cài pyserial.

Mặc định: /dev/ttyUSB0, 19200 8N1, slave ID 3, đọc 13 thanh ghi từ 0x0000.

Ví dụ:
    python3 es_vs_01.py                      # đọc liên tục, 2 lần/giây
    python3 es_vs_01.py --once               # đọc một lần
    python3 es_vs_01.py --csv data.csv       # vừa hiển thị vừa ghi CSV
    python3 es_vs_01.py --interval 1.12      # theo chu kỳ cập nhật của cảm biến

Ghi chú theo es-vs-01-assessment.md:
  * Cảm biến cập nhật giá trị mỗi ~1118 ms và ngừng trả lời bus ~250-490 ms
    trong mỗi chu kỳ. Đây là hành vi bình thường, nên timeout mỗi lần là
    200 ms và tự thử lại, không coi là mất kết nối.
  * Mọi giá trị là số nguyên đã nhân 10. Nhiệt độ là số có dấu (bù hai).
"""

import argparse
import csv
import errno
import fcntl
import os
import select
import struct
import sys
import termios
import time
from datetime import datetime

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 19200
DEFAULT_SLAVE = 3

REG_START = 0x0000
REG_COUNT = 13  # 0x0000 .. 0x000C

BAUD_CONSTANTS = {
    1200: termios.B1200,
    2400: termios.B2400,
    4800: termios.B4800,
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}

CSV_FIELDS = [
    "time", "temperature",
    "speed_x", "speed_y", "speed_z",
    "pos_x", "pos_y", "pos_z",
    "accel_x", "accel_y", "accel_z",
    "version",
]


class ModbusError(Exception):
    """Lỗi giao tiếp Modbus (timeout, CRC sai, exception từ slave...)."""


class ModbusTimeout(ModbusError):
    pass


def crc16(data: bytes) -> int:
    """CRC-16/MODBUS."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusRtuSerial:
    def __init__(self, port: str, baud: int, timeout: float = 0.2):
        if baud not in BAUD_CONSTANTS:
            raise ValueError(f"Baud không hỗ trợ: {baud}")
        self.port = port
        self.timeout = timeout
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise ModbusError(f"{port} đang được chương trình khác sử dụng") from exc
            raise
        self._configure(baud)

    def _configure(self, baud: int) -> None:
        attrs = termios.tcgetattr(self.fd)
        iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = attrs
        # Raw mode, 8N1, không flow control
        iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP
                   | termios.INLCR | termios.IGNCR | termios.ICRNL
                   | termios.IXON | termios.IXOFF | termios.IXANY | termios.INPCK)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON
                   | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        speed = BAUD_CONSTANTS[baud]
        termios.tcsetattr(self.fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, speed, speed, cc])
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read_exact(self, n: int, deadline: float) -> bytes:
        buf = b""
        while len(buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                break
            try:
                chunk = os.read(self.fd, n - len(buf))
            except BlockingIOError:
                continue
            if not chunk:
                # select báo sẵn sàng nhưng chưa có byte nào: đợi tiếp tới hết deadline
                time.sleep(0.001)
                continue
            buf += chunk
        return buf

    def read_registers(self, slave: int, start: int, count: int, function: int = 0x03):
        """Đọc `count` thanh ghi (FC 0x03/0x04). Trả về list số nguyên không dấu."""
        request = struct.pack(">BBHH", slave, function, start, count)
        request += struct.pack("<H", crc16(request))

        termios.tcflush(self.fd, termios.TCIFLUSH)
        os.write(self.fd, request)
        termios.tcdrain(self.fd)

        deadline = time.monotonic() + self.timeout
        header = self._read_exact(2, deadline)
        if len(header) < 2:
            raise ModbusTimeout("Không có phản hồi (timeout)")

        if header[1] & 0x80:  # exception response: addr, fc|0x80, code, crc(2)
            rest = self._read_exact(3, deadline)
            frame = header + rest
            if len(frame) < 5:
                raise ModbusTimeout("Phản hồi exception bị cụt")
            if struct.unpack("<H", frame[3:5])[0] != crc16(frame[:3]):
                raise ModbusError("CRC sai ở phản hồi exception")
            raise ModbusError(f"Slave trả exception code 0x{frame[2]:02X}")

        expected = 3 + 2 * count + 2
        frame = header + self._read_exact(expected - 2, deadline)
        if len(frame) < expected:
            raise ModbusTimeout(f"Phản hồi bị cụt ({len(frame)}/{expected} byte)")
        if struct.unpack("<H", frame[-2:])[0] != crc16(frame[:-2]):
            raise ModbusError("CRC sai")
        if frame[0] != slave or frame[1] != function or frame[2] != 2 * count:
            raise ModbusError(f"Phản hồi không khớp yêu cầu: {frame[:3].hex(' ')}")
        return list(struct.unpack(f">{count}H", frame[3:3 + 2 * count]))


    def write_register(self, slave: int, address: int, value: int) -> None:
        """Ghi một thanh ghi (FC 0x06). Slave echo lại đúng khung yêu cầu."""
        request = struct.pack(">BBHH", slave, 0x06, address, value)
        request += struct.pack("<H", crc16(request))

        termios.tcflush(self.fd, termios.TCIFLUSH)
        os.write(self.fd, request)
        termios.tcdrain(self.fd)

        reply = self._read_exact(len(request), time.monotonic() + self.timeout)
        if len(reply) == 0:
            raise ModbusTimeout("Không có phản hồi khi ghi (timeout)")
        if reply != request:
            raise ModbusError(f"Phản hồi ghi không khớp: {reply.hex(' ')}")


REG_ADDRESS = 0x07D0  # 2000: địa chỉ thiết bị
REG_BAUD = 0x07D1     # 2001: mã baud
BAUD_CODES = {1200: 7, 2400: 0, 4800: 1, 9600: 2, 19200: 3, 38400: 4, 57600: 5, 115200: 6}


def retry(fn, retries: int):
    last = None
    for _ in range(retries + 1):
        try:
            return fn()
        except ModbusError as exc:
            last = exc
    raise last


def to_signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def decode(regs):
    """Giải mã 13 thanh ghi (0x0000..0x000C) thành giá trị vật lý."""
    scale = lambda v: v / 10.0
    return {
        "temperature": scale(to_signed(regs[0x0000])),  # °C
        "speed_x": scale(regs[0x0001]),   # mm/s
        "speed_y": scale(regs[0x0002]),
        "speed_z": scale(regs[0x0003]),
        "pos_x": scale(regs[0x0004]),     # µm
        "pos_y": scale(regs[0x0005]),
        "pos_z": scale(regs[0x0006]),
        "version": regs[0x0009],
        "accel_x": scale(regs[0x000A]),   # m/s²
        "accel_y": scale(regs[0x000B]),
        "accel_z": scale(regs[0x000C]),
    }


def poll(bus: ModbusRtuSerial, slave: int, retries: int, function: int):
    """Đọc một lần, tự thử lại khi timeout/lỗi.

    Cảm biến im lặng ~250-490 ms mỗi chu kỳ 1118 ms; với timeout 200 ms và
    4 lần thử, một lần poll luôn vượt qua được cửa sổ im lặng đó.
    """
    last_error = None
    for _ in range(retries + 1):
        try:
            return decode(bus.read_registers(slave, REG_START, REG_COUNT, function))
        except ModbusError as exc:
            last_error = exc
    raise last_error


def format_line(ts: str, d: dict) -> str:
    return (
        f"{ts}  T={d['temperature']:5.1f}°C  "
        f"v[mm/s]=({d['speed_x']:5.1f},{d['speed_y']:5.1f},{d['speed_z']:5.1f})  "
        f"s[µm]=({d['pos_x']:6.1f},{d['pos_y']:6.1f},{d['pos_z']:6.1f})  "
        f"a[m/s²]=({d['accel_x']:5.1f},{d['accel_y']:5.1f},{d['accel_z']:5.1f})"
    )


def set_baud(args) -> int:
    """Đổi baud của cảm biến: ghi mã vào thanh ghi 0x07D1 (2001) rồi kiểm tra lại
    ở baud mới. `--baud` là baud hiện tại của cảm biến."""
    target = args.set_baud
    code = BAUD_CODES[target]
    with ModbusRtuSerial(args.port, args.baud, args.timeout) as bus:
        current = retry(lambda: bus.read_registers(args.slave, REG_BAUD, 1), 8)[0]
        print(f"Thanh ghi 2001 hiện tại = {current} (baud {args.baud})")
        if current == code:
            print("Đã đúng giá trị, không cần ghi.")
        try:
            retry(lambda: bus.write_register(args.slave, REG_BAUD, code), 8)
            print(f"Đã ghi thanh ghi 2001 = {code} ({target} baud)")
        except ModbusError as exc:
            # Cảm biến có thể đổi baud ngay nên echo bị méo; sẽ kiểm tra ở baud mới.
            print(f"Chưa xác nhận được echo ({exc}); kiểm tra lại ở baud mới.")

    time.sleep(0.5)
    with ModbusRtuSerial(args.port, target, args.timeout) as bus:
        try:
            value = retry(lambda: bus.read_registers(args.slave, REG_BAUD, 1), 8)
        except ModbusError as exc:
            print(f"Không đọc được ở {target} baud: {exc}", file=sys.stderr)
            print("Có thể cảm biến cần tắt/mở nguồn để áp dụng baud mới.", file=sys.stderr)
            return 1
    print(f"Xác nhận ở {target} baud: thanh ghi 2001 = {value[0]}")
    return 0 if value[0] == code else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Đọc cảm biến rung ES-VS-01 qua Modbus RTU")
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--slave", type=int, default=DEFAULT_SLAVE, help="Slave ID (mặc định 3)")
    ap.add_argument("--interval", type=float, default=0.5, help="Chu kỳ poll, giây (mặc định 0.5)")
    ap.add_argument("--timeout", type=float, default=0.2, help="Timeout mỗi giao dịch, giây (mặc định 0.2)")
    ap.add_argument("--retries", type=int, default=4, help="Số lần thử lại mỗi poll (mặc định 4)")
    ap.add_argument("--function", type=lambda x: int(x, 0), default=0x03, choices=[0x03, 0x04],
                    help="Function code 0x03 hoặc 0x04")
    ap.add_argument("--once", action="store_true", help="Đọc một lần rồi thoát")
    ap.add_argument("--count", type=int, default=0, help="Số lần đọc (0 = vô hạn)")
    ap.add_argument("--csv", help="Ghi thêm vào file CSV")
    ap.add_argument("--set-baud", type=int, choices=sorted(BAUD_CODES),
                    help="Đổi baud của cảm biến (ghi thanh ghi 2001) rồi thoát. "
                         "--baud là baud hiện tại của cảm biến")
    args = ap.parse_args()

    if args.set_baud:
        try:
            return set_baud(args)
        except (OSError, ModbusError) as exc:
            print(f"Lỗi: {exc}", file=sys.stderr)
            return 2

    try:
        bus = ModbusRtuSerial(args.port, args.baud, args.timeout)
    except (OSError, ModbusError, ValueError) as exc:
        print(f"Không mở được {args.port}: {exc}", file=sys.stderr)
        return 2

    csv_file = writer = None
    if args.csv:
        is_new = not os.path.exists(args.csv) or os.path.getsize(args.csv) == 0
        csv_file = open(args.csv, "a", newline="")
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()

    print(f"Kết nối {args.port} @ {args.baud} 8N1, slave ID {args.slave}. Ctrl+C để dừng.",
          file=sys.stderr)

    limit = 1 if args.once else args.count
    done = failures = 0
    exit_code = 0
    try:
        while limit == 0 or done < limit:
            started = time.monotonic()
            ts = datetime.now().isoformat(timespec="milliseconds")
            try:
                data = poll(bus, args.slave, args.retries, args.function)
                failures = 0
                exit_code = 0
                print(format_line(ts, data), flush=True)
                if writer:
                    writer.writerow({"time": ts, **data})
                    csv_file.flush()
            except ModbusError as exc:
                failures += 1
                exit_code = 1
                print(f"{ts}  LỖI: {exc} (liên tiếp {failures} poll)", file=sys.stderr, flush=True)
            done += 1
            if limit == 0 or done < limit:
                time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nĐã dừng.", file=sys.stderr)
        exit_code = 0
    finally:
        bus.close()
        if csv_file:
            csv_file.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

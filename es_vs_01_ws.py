#!/usr/bin/env python3
"""Nhận dữ liệu cảm biến rung ES-VS-01 qua Socket.IO của iot_core.

Khác với es_vs_01.py (đọc trực tiếp Modbus RTU qua RS485), chương trình này
không nói chuyện với cảm biến. Nó là một Socket.IO client: iot_core đã poll
cảm biến sẵn và phát (broadcast) giá trị, ta chỉ việc đăng ký và nghe.

Tầng truyền (WebSocket + Engine.IO + Socket.IO) nằm ở socketio_client.py,
chỉ dùng thư viện chuẩn của Python — không cần cài python-socketio.

Theo ghi chú dự án:
  * Địa chỉ : Socket.IO iot_core https://orangepi.local
              (nginx chuyển /socket.io sang cổng :3000)
  * Đăng ký : phát sự kiện CONN_SUBSCRIBE dạng {"sn": "VS01",
              "level": "2026010900100098"}
  * Nhận    : sự kiện CONN_DATA dạng {"sn", "level", "data"}
  * Khoá trong data: "<thanh ghi>-3" (slave 3), giá trị THÔ (đã ×10), kèm
              khoá "TS" là giờ của gói. Gói đầu là full, các gói sau chỉ
              chứa những khoá đã đổi -> socketio_client tự gộp (merge).

Bảng thanh ghi (giá trị thô ÷ 10, trừ phiên bản firmware):
  0-3                -> nhiệt độ °C (có dấu, bù hai)
  1-3, 2-3, 3-3      -> vận tốc X/Y/Z, mm/s
  4-3, 5-3, 6-3      -> độ dời X/Y/Z, µm
  9-3                -> phiên bản firmware (vd 2314), KHÔNG chia
  10-3, 11-3, 12-3   -> gia tốc X/Y/Z, m/s²

Ví dụ:
    python3 es_vs_01_ws.py
    python3 es_vs_01_ws.py --url https://orangepi.local --insecure
    python3 es_vs_01_ws.py --csv data.csv
    python3 es_vs_01_ws.py --raw
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime

from socketio_client import SocketIOClient

DEFAULT_URL = "http://127.0.0.1:3000"  # chạy tại chỗ trên Pi; từ máy khác: --url https://orangepi.local
DEFAULT_SN = "VS01"
DEFAULT_LEVEL = "2026010900100098"
SOCKETIO_PATH = "/socket.io/"

SLAVE = 3  # các khoá trong data có dạng "<reg>-3"

# reg -> (tên trường, có dấu?, hệ số chia)
REG_FIELDS = {
    0:  ("temperature", True,  10.0),  # °C
    1:  ("speed_x",     False, 10.0),  # mm/s
    2:  ("speed_y",     False, 10.0),
    3:  ("speed_z",     False, 10.0),
    4:  ("pos_x",       False, 10.0),  # µm
    5:  ("pos_y",       False, 10.0),
    6:  ("pos_z",       False, 10.0),
    9:  ("version",     False, 1.0),   # phiên bản firmware, không chia
    10: ("accel_x",     False, 10.0),  # m/s²
    11: ("accel_y",     False, 10.0),
    12: ("accel_z",     False, 10.0),
}

CSV_FIELDS = [
    "time", "ts", "sn", "level", "temperature",
    "speed_x", "speed_y", "speed_z",
    "pos_x", "pos_y", "pos_z",
    "accel_x", "accel_y", "accel_z",
    "version",
]


# ---------------------------------------------------------------------------
# Giải mã dữ liệu đo
# ---------------------------------------------------------------------------

def to_signed16(value: int) -> int:
    """Chuyển giá trị 16-bit không dấu sang có dấu (bù hai).

    JSON có thể gửi sẵn số âm (vd -1) thì giữ nguyên; nếu gửi dạng không dấu
    (vd 65535) thì mới quy đổi."""
    if 0x8000 <= value <= 0xFFFF:
        return value - 0x10000
    return value


def parse_key(key: str):
    """'0-3' -> (reg=0, slave=3). Trả None nếu không phải khoá thanh ghi."""
    reg_str, sep, slave_str = key.rpartition("-")
    if not sep or not slave_str.isdigit():
        return None
    reg_str = reg_str.strip()
    if not reg_str.lstrip("-").isdigit():
        return None
    return int(reg_str), int(slave_str)


def decode(state: dict) -> dict:
    """Từ trạng thái thô đã gộp (khoá '<reg>-<slave>' -> số thô) ra giá trị vật lý."""
    out = {name: None for name, _, _ in REG_FIELDS.values()}
    for key, raw in state.items():
        if key == "TS":
            continue
        parsed = parse_key(key)
        if parsed is None:
            continue
        reg, _slave = parsed
        field = REG_FIELDS.get(reg)
        if field is None:
            continue
        name, signed, divisor = field
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if signed:
            value = to_signed16(value)
        out[name] = value / divisor if divisor != 1.0 else value
    return out


def format_line(ts: str, sn, d: dict) -> str:
    def g(name, width, prec):
        v = d.get(name)
        return f"{v:{width}.{prec}f}" if isinstance(v, float) else f"{'--':>{width}}"

    ver = d.get("version")
    ver_s = f"fw={ver}" if ver is not None else "fw=--"
    return (
        f"{ts}  [{sn}]  "
        f"T={g('temperature',5,1)}°C  "
        f"v[mm/s]=({g('speed_x',5,1)},{g('speed_y',5,1)},{g('speed_z',5,1)})  "
        f"s[µm]=({g('pos_x',6,1)},{g('pos_y',6,1)},{g('pos_z',6,1)})  "
        f"a[m/s²]=({g('accel_x',5,1)},{g('accel_y',5,1)},{g('accel_z',5,1)})  "
        f"{ver_s}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nhận dữ liệu ES-VS-01 qua Socket.IO của iot_core")
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"Địa chỉ iot_core (mặc định {DEFAULT_URL})")
    ap.add_argument("--path", default=SOCKETIO_PATH,
                    help=f"Đường dẫn Socket.IO (mặc định {SOCKETIO_PATH})")
    ap.add_argument("--sn", default=DEFAULT_SN, help=f"Serial number (mặc định {DEFAULT_SN})")
    ap.add_argument("--level", default=DEFAULT_LEVEL,
                    help=f"Mã level khi đăng ký (mặc định {DEFAULT_LEVEL})")
    ap.add_argument("--insecure", action="store_true",
                    help="Bỏ qua kiểm tra chứng chỉ TLS (cho chứng chỉ tự ký)")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="Timeout TCP/đọc ban đầu, giây (mặc định 30)")
    ap.add_argument("--reconnect", type=float, default=3.0,
                    help="Giãn cách thử kết nối lại, giây (mặc định 3; 0 = không thử lại)")
    ap.add_argument("--csv", help="Ghi thêm vào file CSV")
    ap.add_argument("--raw", action="store_true", help="In thêm gói JSON thô nhận được")
    ap.add_argument("--no-filter", action="store_true",
                    help="Nhận mọi CONN_DATA, không lọc theo sn/level")
    ap.add_argument("--once", action="store_true",
                    help="Nhận đúng 1 gói CONN_DATA rồi thoát")
    args = ap.parse_args()

    csv_file = writer = None
    if args.csv:
        is_new = not os.path.exists(args.csv) or os.path.getsize(args.csv) == 0
        csv_file = open(args.csv, "a", newline="")
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()

    counter = {"n": 0}

    def on_data(sn, level, merged):
        if not args.no_filter:
            if args.sn and sn != args.sn:
                return
            if args.level and level is not None and level != args.level:
                return
        decoded = decode(merged)
        now = datetime.now().isoformat(timespec="milliseconds")
        print(format_line(now, sn, decoded), flush=True)
        if args.raw:
            print("   raw:", json.dumps({"sn": sn, "level": level, "data": merged},
                                         ensure_ascii=False), flush=True)
        if writer:
            row = {"time": now, "ts": merged.get("TS"), "sn": sn, "level": level}
            row.update({k: v for k, v in decoded.items() if k in CSV_FIELDS})
            writer.writerow(row)
            csv_file.flush()
        counter["n"] += 1
        if args.once and counter["n"] >= 1:
            client.stop()

    def on_connect():
        print(f"Đã đăng ký CONN_SUBSCRIBE sn={args.sn} level={args.level}. "
              f"Ctrl+C để dừng.", file=sys.stderr)

    def on_disconnect(exc):
        print(f"Mất kết nối: {exc}", file=sys.stderr, flush=True)

    client = SocketIOClient(
        args.url, [{"sn": args.sn, "level": args.level}], on_data,
        path=args.path, insecure=args.insecure, timeout=args.timeout,
        on_connect=on_connect, on_disconnect=on_disconnect)

    print(f"Đang kết nối {client._ws_url()} ...", file=sys.stderr)
    try:
        client.run_forever(reconnect=args.reconnect)
    except KeyboardInterrupt:
        print("\nĐã dừng.", file=sys.stderr)
        client.stop()
    finally:
        if csv_file:
            csv_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

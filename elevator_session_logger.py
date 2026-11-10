#!/usr/bin/env python3
"""Thu thập dữ liệu cảm biến rung ES-VS-01 cắt theo từng phiên chạy của thang máy.

Thiết kế theo elevator-session-vibration-plan.md (bản đã chốt).

Hai nguồn, cùng một kết nối Socket.IO tới iot_core:
  * Rung  : sn=VS01 (gắn trên car 1). Key "<reg>-3", thô ÷10, xem es_vs_01_ws.
  * Thang : sn=2026010900100098 ("SmartCabin", gộp 2 cabin). Key "<reg thập phân>-<cabin>".
            Chỉ dùng car 1:
              8480-1  trạng thái (bit2=chạy, bit0=lên, bit1=xuống) — giá trị thô, đọc theo bit
              8473-1  tốc độ chạy (mm/s, không chia)
              8465-1  tầng hiện tại
              8448-1/8449-1  tổng số lần chạy (32-bit = cao<<16 | thấp)

Phát hiện phiên (máy trạng thái):
  - Bắt đầu: bit2=1 VÀ speed > NGUONG_CHAY  -> mở phiên, ghi hướng/tầng đi/run_number.
  - Kết thúc: bit2=0 VÀ speed ≈ 0 ổn định   -> đóng phiên sau cửa sổ post-roll.
  - Lọc rác: thời lượng < MIN_DURATION thì bỏ.
  - Pre-roll/post-roll: giữ thêm vài giây mẫu rung trước/sau phiên.

Lưu (phương án A): mỗi phiên một JSON + một index.csv tổng hợp. Chỉ ghi phiên chạy.

Ví dụ:
    python3 elevator_session_logger.py
    python3 elevator_session_logger.py --url https://orangepi.local --insecure
    python3 elevator_session_logger.py --outdir data/sessions --pre-roll 2 --post-roll 2
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime

from socketio_client import SocketIOClient
from es_vs_01_ws import decode as decode_vibration

# ---- Mặc định (plan §8) ----
DEFAULT_URL = "http://127.0.0.1:3000"
SOCKETIO_PATH = "/socket.io/"

VIB_SN = "VS01"
VIB_LEVEL = "2026010900100098"
ELEV_SN = "2026010900100098"
ELEV_LEVEL = "2026010900100098"
CAR = 1  # VS01 gắn trên car 1

NGUONG_CHAY = 20.0     # mm/s — dưới ngưỡng này coi như chưa chạy (lọc releveling)
SPEED_ZERO = 5.0       # mm/s — coi như đã dừng hẳn
MIN_DURATION = 1.5     # s — phiên ngắn hơn bị loại (jog/releveling)
T_DEBOUNCE = 0.8       # s — gộp khoảng dừng ngắn trong post-roll
PRE_ROLL_S = 2.0
POST_ROLL_S = 2.0

TICK_INTERVAL = 0.2    # s — nhịp kiểm tra deadline đóng phiên

# Key thanh ghi thang (car mặc định điền lúc chạy)
REG_STATUS = 8480   # 0x2120
REG_SPEED = 8473    # 0x2119
REG_FLOOR = 8465    # 0x2111
REG_RUN_LO = 8448   # 0x2100
REG_RUN_HI = 8449   # 0x2101

INDEX_FIELDS = [
    "session_id", "start_local", "end_local", "duration_s", "direction",
    "speed_max_mm_s", "floor_from", "floor_to", "run_number", "n_vib", "incomplete",
]


# ---------------------------------------------------------------------------
# Giải mã trạng thái thang (một cabin)
# ---------------------------------------------------------------------------

def _get_int(state, key):
    v = state.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def decode_elevator(state: dict, car: int):
    """Rút trạng thái car `car` từ state thô đã gộp. Trả None nếu thiếu 8480."""
    status = _get_int(state, f"{REG_STATUS}-{car}")
    if status is None:
        return None
    speed = _get_int(state, f"{REG_SPEED}-{car}")
    floor = _get_int(state, f"{REG_FLOOR}-{car}")
    run_lo = _get_int(state, f"{REG_RUN_LO}-{car}")
    run_hi = _get_int(state, f"{REG_RUN_HI}-{car}")
    run_number = None
    if run_lo is not None and run_hi is not None:
        run_number = (run_hi << 16) | run_lo
    up = bool(status & 0x0001)
    down = bool(status & 0x0002)
    return {
        "raw_2120": status,
        "running": bool(status & 0x0004),
        "dir": "up" if up else "down" if down else None,
        "maintenance": bool(status & 0x0008),
        "fault": bool(status & 0x0020),
        "speed_mm_s": speed if speed is not None else 0,
        "floor": floor,
        "run_number": run_number,
    }


def _vib_sample(now_iso, ts_utc, decoded, phase):
    return {
        "t": now_iso, "ts_utc": ts_utc, "phase": phase,
        "temp": decoded.get("temperature"),
        "vx": decoded.get("speed_x"), "vy": decoded.get("speed_y"), "vz": decoded.get("speed_z"),
        "px": decoded.get("pos_x"), "py": decoded.get("pos_y"), "pz": decoded.get("pos_z"),
        "ax": decoded.get("accel_x"), "ay": decoded.get("accel_y"), "az": decoded.get("accel_z"),
    }


# ---------------------------------------------------------------------------
# Quản lý phiên
# ---------------------------------------------------------------------------

IDLE, RUNNING, CLOSING = "IDLE", "RUNNING", "CLOSING"


class SessionManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.state = IDLE
        self.session = None
        self.start_dt = None
        self.stop_dt = None
        self.close_deadline = None  # time.monotonic()
        self.vib_ring = deque()     # (recv_monotonic, sample) cho pre-roll
        self.seen_stopped = False   # đã thấy trạng thái dừng kể từ khi kết nối?
        os.makedirs(cfg.outdir, exist_ok=True)
        self.index_path = os.path.join(cfg.outdir, "index.csv")
        self._ensure_index()

    def _ensure_index(self):
        is_new = not os.path.exists(self.index_path) or os.path.getsize(self.index_path) == 0
        if is_new:
            with open(self.index_path, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=INDEX_FIELDS).writeheader()

    # ---- callbacks từ SocketIOClient (chạy ở thread client) ----

    def on_vibration(self, merged):
        now = datetime.now()
        decoded = decode_vibration(merged)
        ts_utc = merged.get("TS")
        with self.lock:
            mono = time.monotonic()
            # luôn nạp ring cho pre-roll, cắt theo PRE_ROLL_S
            self.vib_ring.append((mono, now.isoformat(timespec="milliseconds"),
                                  ts_utc, decoded))
            cutoff = mono - self.cfg.pre_roll
            while self.vib_ring and self.vib_ring[0][0] < cutoff:
                self.vib_ring.popleft()
            if self.session is not None and self.state in (RUNNING, CLOSING):
                phase = "run" if self.state == RUNNING else "post"
                self.session["vibration"].append(
                    _vib_sample(now.isoformat(timespec="milliseconds"),
                                ts_utc, decoded, phase))

    def on_elevator(self, merged):
        now = datetime.now()
        ctrl = decode_elevator(merged, self.cfg.car)
        if ctrl is None:
            return
        with self.lock:
            is_moving = ctrl["running"] and ctrl["speed_mm_s"] > self.cfg.nguong_chay
            is_stopped = (not ctrl["running"]) and ctrl["speed_mm_s"] <= SPEED_ZERO
            if is_stopped:
                self.seen_stopped = True

            if self.state == IDLE:
                if is_moving:
                    self._open(now, ctrl)
            elif self.state == RUNNING:
                self._track(ctrl)
                if is_stopped:
                    self.state = CLOSING
                    self.stop_dt = now
                    self.close_deadline = time.monotonic() + max(
                        self.cfg.post_roll, T_DEBOUNCE)
            elif self.state == CLOSING:
                self._track(ctrl)
                if is_moving:  # chạy tiếp -> gộp lại, hủy đóng
                    self.state = RUNNING
                    self.stop_dt = None
                    self.close_deadline = None

            # ghi mẫu trạng thái vào phiên (sau khi máy trạng thái có thể vừa mở phiên,
            # để điểm mở phiên cũng được lưu)
            if self.session is not None:
                self.session["controller"].append({
                    "t": now.isoformat(timespec="milliseconds"),
                    "raw_2120": ctrl["raw_2120"], "running": ctrl["running"],
                    "dir": ctrl["dir"], "speed_mm_s": ctrl["speed_mm_s"],
                    "floor": ctrl["floor"],
                })

    def on_connect(self):
        with self.lock:
            self.seen_stopped = False

    def on_disconnect(self, exc):
        with self.lock:
            print(f"Mất kết nối: {exc}", file=sys.stderr, flush=True)
            if self.session is not None:
                # đóng phiên dở, đánh dấu incomplete
                self.session["incomplete"] = True
                end = self.stop_dt or datetime.now()
                self._finalize(end, forced=True)

    # ---- nhịp kiểm tra deadline (chạy ở thread ticker) ----

    def tick(self):
        with self.lock:
            if self.state == CLOSING and self.close_deadline is not None \
                    and time.monotonic() >= self.close_deadline:
                self._finalize(self.stop_dt or datetime.now())

    # ---- nội bộ (gọi khi đã giữ lock) ----

    def _open(self, now, ctrl):
        sid = now.strftime("%Y-%m-%dT%H-%M-%S")
        self.start_dt = now
        self.session = {
            "session_id": sid,
            "car": self.cfg.car,
            "direction": ctrl["dir"],
            "start_local": now.isoformat(timespec="milliseconds"),
            "end_local": None,
            "duration_s": None,
            "speed_max_mm_s": ctrl["speed_mm_s"],
            "floor_from": ctrl["floor"],
            "floor_to": ctrl["floor"],
            "run_number": ctrl["run_number"],
            "pre_roll_s": self.cfg.pre_roll,
            "post_roll_s": self.cfg.post_roll,
            "flags": {"maintenance": ctrl["maintenance"], "fault": ctrl["fault"]},
            "incomplete": not self.seen_stopped,  # vào giữa chuyến lúc khởi động
            "vibration": [],
            "controller": [],
        }
        # nạp pre-roll từ ring
        for _mono, t_iso, ts_utc, decoded in self.vib_ring:
            self.session["vibration"].append(_vib_sample(t_iso, ts_utc, decoded, "pre"))
        self.state = RUNNING

    def _track(self, ctrl):
        s = self.session
        if s is None:
            return
        if ctrl["speed_mm_s"] > s["speed_max_mm_s"]:
            s["speed_max_mm_s"] = ctrl["speed_mm_s"]
        if ctrl["floor"] is not None:
            s["floor_to"] = ctrl["floor"]
        if s["direction"] is None and ctrl["dir"]:
            s["direction"] = ctrl["dir"]
        if ctrl["maintenance"]:
            s["flags"]["maintenance"] = True
        if ctrl["fault"]:
            s["flags"]["fault"] = True

    def _finalize(self, end_dt, forced=False):
        s = self.session
        if s is None:
            self._reset()
            return
        duration = (end_dt - self.start_dt).total_seconds() if self.start_dt else 0.0
        s["end_local"] = end_dt.isoformat(timespec="milliseconds")
        s["duration_s"] = round(duration, 3)

        # Lọc phiên quá ngắn (jog/releveling, hoặc đuôi chuyển động bắt được lúc
        # khởi động). Chỉ bỏ qua bộ lọc khi mất kết nối giữa chừng (forced): đó là
        # một chuyến thật bị cắt cụt phía ta, nên vẫn giữ lại dù ngắn.
        if not forced and duration < self.cfg.min_duration:
            tag = " incomplete" if s["incomplete"] else ""
            print(f"Bỏ phiên rác{tag} (thời lượng {duration:.2f}s < {self.cfg.min_duration}s)",
                  file=sys.stderr, flush=True)
            self._reset()
            return

        self._write(s)
        print(f"Phiên {s['session_id']} [{s['direction']}] {duration:.2f}s "
              f"tầng {s['floor_from']}->{s['floor_to']} v_max={s['speed_max_mm_s']}mm/s "
              f"n_vib={len(s['vibration'])}"
              + ("  (INCOMPLETE)" if s["incomplete"] else ""),
              file=sys.stderr, flush=True)
        self._reset()

    def _reset(self):
        self.state = IDLE
        self.session = None
        self.start_dt = None
        self.stop_dt = None
        self.close_deadline = None

    def _write(self, s):
        direction = s["direction"] or "na"
        fname = f"{s['session_id']}_{direction}.json"
        path = os.path.join(self.cfg.outdir, fname)
        # tránh ghi đè nếu trùng id (hai phiên trong cùng một giây)
        n = 1
        while os.path.exists(path):
            path = os.path.join(self.cfg.outdir, f"{s['session_id']}_{direction}_{n}.json")
            n += 1
        with open(path, "w") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        with open(self.index_path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=INDEX_FIELDS).writerow({
                "session_id": s["session_id"],
                "start_local": s["start_local"], "end_local": s["end_local"],
                "duration_s": s["duration_s"], "direction": s["direction"],
                "speed_max_mm_s": s["speed_max_mm_s"],
                "floor_from": s["floor_from"], "floor_to": s["floor_to"],
                "run_number": s["run_number"], "n_vib": len(s["vibration"]),
                "incomplete": s["incomplete"],
            })


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Thu thập rung ES-VS-01 cắt theo phiên chạy thang máy (car 1)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--path", default=SOCKETIO_PATH)
    ap.add_argument("--insecure", action="store_true",
                    help="Bỏ qua kiểm tra chứng chỉ TLS (chứng chỉ tự ký)")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--reconnect", type=float, default=3.0)
    ap.add_argument("--outdir", default="data/sessions", help="Thư mục lưu phiên")
    ap.add_argument("--car", type=int, default=CAR, help="Cabin theo dõi (mặc định 1)")
    ap.add_argument("--vib-sn", default=VIB_SN)
    ap.add_argument("--vib-level", default=VIB_LEVEL)
    ap.add_argument("--elev-sn", default=ELEV_SN)
    ap.add_argument("--elev-level", default=ELEV_LEVEL)
    ap.add_argument("--nguong-chay", type=float, default=NGUONG_CHAY,
                    help=f"Ngưỡng tốc độ coi là đang chạy, mm/s (mặc định {NGUONG_CHAY})")
    ap.add_argument("--min-duration", type=float, default=MIN_DURATION,
                    help=f"Thời lượng tối thiểu của phiên, giây (mặc định {MIN_DURATION})")
    ap.add_argument("--pre-roll", type=float, default=PRE_ROLL_S,
                    help=f"Đệm trước phiên, giây (mặc định {PRE_ROLL_S})")
    ap.add_argument("--post-roll", type=float, default=POST_ROLL_S,
                    help=f"Đệm sau phiên, giây (mặc định {POST_ROLL_S})")
    args = ap.parse_args()

    mgr = SessionManager(args)

    def on_data(sn, level, merged):
        if sn == args.vib_sn:
            mgr.on_vibration(merged)
        elif sn == args.elev_sn:
            mgr.on_elevator(merged)

    subs = [
        {"sn": args.vib_sn, "level": args.vib_level},
        {"sn": args.elev_sn, "level": args.elev_level},
    ]
    client = SocketIOClient(
        args.url, subs, on_data, path=args.path, insecure=args.insecure,
        timeout=args.timeout,
        on_connect=lambda: (mgr.on_connect(),
                            print(f"Đã subscribe VS01 + thang (car {args.car}). "
                                  f"Đang chờ phiên chạy... Ctrl+C để dừng.",
                                  file=sys.stderr)),
        on_disconnect=mgr.on_disconnect)

    # thread nhịp kiểm tra deadline đóng phiên
    stop_flag = threading.Event()

    def ticker():
        while not stop_flag.is_set():
            time.sleep(TICK_INTERVAL)
            mgr.tick()

    t = threading.Thread(target=ticker, daemon=True)
    t.start()

    print(f"Đang kết nối {client._ws_url()} ...", file=sys.stderr)
    print(f"Lưu phiên vào: {os.path.abspath(args.outdir)}", file=sys.stderr)
    try:
        client.run_forever(reconnect=args.reconnect)
    except KeyboardInterrupt:
        print("\nĐã dừng.", file=sys.stderr)
        client.stop()
    finally:
        stop_flag.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())

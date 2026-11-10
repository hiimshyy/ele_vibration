# Plan: Thu thập dữ liệu cảm biến rung theo từng phiên hoạt động của thang máy

> Trạng thái: **bản plan đã chốt, chờ review — chưa code.**
> Liên quan: `es_vs_01_ws.py` (nhận rung qua Socket.IO), `Address_Elevator.md` (bảng địa chỉ biến tần), `es-vs-01-assessment.md` (giới hạn của cảm biến rung).

---

## 1. Mục tiêu

Ghi lại dữ liệu cảm biến rung ES-VS-01 (VS01) **cắt theo từng phiên chạy của thang máy car 1**: mỗi lần cabin bắt đầu chạy đến khi dừng là một "phiên" (session/trip), kèm hướng chạy (lên/xuống) và tốc độ trong suốt phiên.

Mỗi phiên lưu:
- Siêu dữ liệu: id phiên, thời điểm bắt đầu/kết thúc, thời lượng, hướng, tốc độ đỉnh, tầng đi–đến, số thứ tự chuyến.
- Chuỗi mẫu rung: nhiệt độ, vận tốc X/Y/Z, độ dời X/Y/Z, gia tốc X/Y/Z.
- Chuỗi trạng thái thang: cờ `8480-1`, tốc độ `8473-1`.

Giá trị: `es-vs-01-assessment.md` kết luận cảm biến rung **không tự** phân đoạn chuyến (chu kỳ nội bộ 1118 ms). Controller cung cấp mốc bắt đầu/kết thúc chính xác, nên hợp hai nguồn cho ta trip segmentation thật.

---

## 2. Hai nguồn dữ liệu — CẢ HAI qua Socket.IO iot_core

Một kết nối Socket.IO, subscribe hai thiết bị, cùng nghe sự kiện `CONN_DATA`. Mỗi thiết bị đẩy gói full đầu tiên rồi các gói delta khi có thay đổi ⇒ giữ state gộp **riêng cho từng sn** (như `es_vs_01_ws.py` đang làm cho rung).

### 2.1 Luồng rung — VS01 (gắn trên car 1)
- `CONN_SUBSCRIBE {sn:"VS01", level:"2026010900100098"}` → `CONN_DATA`.
- Key `<reg>-3` (ADDR 3): `0-3` nhiệt độ, `1-3..3-3` vận tốc, `4-3..6-3` độ dời, `10-3..12-3` gia tốc. Thô ÷10 (trừ `9-3` firmware).
- Nhịp ~1 giá trị mới mỗi **1118 ms**. Gói kèm `TS` — **giờ UTC** (lệch +7 so với local Pi). Xem §5.

### 2.2 Luồng trạng thái thang — "SmartCabin" sn `2026010900100098` (ĐÃ xác nhận bằng gói thật 2026-11-10)
- `CONN_SUBSCRIBE {sn:"2026010900100098", level:"2026010900100098"}` → `CONN_DATA`, Name="SmartCabin".
- Thiết bị gộp **2 cabin**: key `<reg>-1` (car 1) và `<reg>-2` (car 2). **Chỉ dùng car 1** (VS01 gắn ở đó).
- Socket gửi **giá trị RAW**, scale/bit do ta tự áp.

| Thanh ghi | Key car 1 | Scale/đọc | Dùng để |
|---|---|---|---|
| `0x2120` Trạng thái (8480) | `8480-1` | đọc theo bit | **bit2=chạy; bit0=lên; bit1=xuống** |
| `0x2119` Running Speed (8473) | `8473-1` | **mm/s, không chia** | tốc độ hiện tại |
| `0x2111` Tầng (8465) | `8465-1` | trực tiếp | tầng đi → tầng đến |
| `0x2100`/`0x2101` Số lần chạy (8448/8449) | `8448-1`,`8449-1` | 32-bit `cao<<16 \| thấp` | xác nhận/đếm phiên |
| `0x2110` Chiều cao (8464) | `8464-1` | **/100 → m** | (phụ) vị trí/quãng đường |
| `0x2113` Bằng tầng (8467) | `8467-1` | bit14 | (phụ) dừng đúng tầng |

Mask bit `0x2120`: `RUNNING = v&0x0004`, `UP = v&0x0001`, `DOWN = v&0x0002`, (nhãn) `MAINTENANCE = v&0x0008`, `FAULT = v&0x0020`.

> Kiểm chứng lúc đứng yên: `8480-1`=1280 (bit2=0), `8473-1`=0, `8465-1`=3 (tầng 3), `8464-1`=817 (8.17 m), `8448/8449-1` ≈ 507.793 chuyến. Tên sự kiện vẫn `CONN_DATA`; `Mode` chỉ là nhãn thông tin.

---

## 3. Máy trạng thái phát hiện phiên (event-driven, không poll)

Mỗi khi `8480-1`/`8473-1` đổi, gói delta tự đến; cập nhật state rồi chạy:

```
IDLE:
    khi RUNNING(8480-1 bit2)==1 VÀ speed(8473-1) > NGUONG_CHAY:
        mở phiên; ghi t_start, hướng (bit0/bit1), tầng_đi (8465-1), run_number (8448/8449-1)
        -> RUNNING

RUNNING:
    gắn mọi mẫu rung + mẫu trạng thái tới vào phiên hiện tại; cập nhật tốc độ đỉnh
    khi RUNNING(bit2)==0 VÀ speed≈0 ổn định > T_DEBOUNCE:
        đóng phiên; ghi t_end, thời lượng, tầng_đến (8465-1)
        nếu thời lượng < MIN_DURATION -> loại (phiên rác); ngược lại kết xuất ra đĩa
        -> IDLE
```

Chống nhiễu:
- "Đang chạy" = `bit2=1` VÀ `speed > NGUONG_CHAY` → bỏ qua releveling, rung cửa.
- "Dừng" = `bit2=0` VÀ `speed ≈ 0` giữ ổn định trong `T_DEBOUNCE` → không cắt một chuyến thành nhiều mảnh.
- Đối chiếu `8448/8449-1` tăng 1 để xác nhận một chuyến hợp lệ.

---

## 4. Kiến trúc chương trình

Một tiến trình, một kết nối Socket.IO, subscribe 2 sn. Router theo sn:

```
                     +--(sn=VS01)-------------> giải mã rung ------------+
 [Socket.IO] --------+                                                    +--> [Session Manager] --> [ghi đĩa]
   CONN_DATA         +--(sn=2026...098, car1)--> giải mã 8480-1/8473-1 ---+          |
                                                 + máy trạng thái §3 ---------------+
```

- Tái dùng tầng WebSocket/Engine.IO/Socket.IO trong `es_vs_01_ws.py`; tách phần nhận/giải mã thành module dùng chung, hỗ trợ subscribe nhiều sn, giữ state theo sn.
- Máy trạng thái §3 chạy trên state của thang (car 1); mỗi mẫu rung tới được gắn vào phiên đang mở (nếu có).
- Tự kết nối lại khi rớt. Socket rớt giữa phiên → đánh dấu phiên `incomplete`.

### 4.1 Pre-roll / post-roll (BẬT, mặc định 2 s mỗi bên)
Giữ vòng đệm các mẫu rung **2 s trước** khi mở phiên và tiếp tục ghi **2 s sau** khi đóng phiên, để bắt transient nhả phanh/hãm phanh và bù độ trễ cảm biến. Chỉnh/tắt bằng tham số `PRE_ROLL_S`, `POST_ROLL_S`.

---

## 5. Đồng bộ thời gian

- Cùng chạy trên một Orange Pi → dùng **đồng hồ local** (thời điểm nhận gói) làm trục chung ghép hai luồng.
- Vẫn lưu `TS` gốc (UTC) của gói rung để truy vết. **Lệch múi giờ**: `TS` là UTC, local UTC+7 — so trực tiếp sẽ lệch 7 giờ.
- Trần độ phân giải: chuyến 3–10 s mà rung ~1 mẫu/1.1 s ⇒ chỉ **~3–9 mẫu rung/phiên** (đúng `es-vs-01-assessment.md`). Giới hạn vật lý của cảm biến, pre/post-roll chỉ thêm ~1–2 mẫu mỗi đầu.

---

## 6. Định dạng lưu — Phương án A (đã chọn)

Mỗi phiên một file JSON + một `index.csv` tổng hợp. **Chỉ ghi phiên chạy** (kèm pre/post-roll); không ghi lúc đứng yên.

```
data/sessions/index.csv
session_id,start_local,end_local,duration_s,direction,speed_max_mm_s,floor_from,floor_to,run_number,n_vib,incomplete
2026-11-09T14-25-03,2026-11-09T14:25:03.512,2026-11-09T14:25:09.740,6.23,up,1021,1,5,507793,6,false
```
```jsonc
// data/sessions/2026-11-09T14-25-03_up.json
{
  "session_id": "2026-11-09T14-25-03",
  "car": 1,
  "direction": "up",
  "start_local": "2026-11-09T14:25:03.512",
  "end_local":   "2026-11-09T14:25:09.740",
  "duration_s": 6.23,
  "speed_max_mm_s": 1021,
  "floor_from": 1, "floor_to": 5, "run_number": 507793,
  "pre_roll_s": 2.0, "post_roll_s": 2.0,
  "incomplete": false,
  "vibration": [
    {"t":"14:25:01.600","ts_utc":"07:25:01.580Z","phase":"pre","temp":40.8,"vx":0.0,"vy":0.0,"vz":0.0,"ax":0.0,"ay":0.0,"az":0.0},
    {"t":"14:25:03.600","ts_utc":"07:25:03.580Z","phase":"run","temp":40.8,"vx":0.3,"vy":0.1,"vz":0.5,"ax":11.0,"ay":9.5,"az":20.0},
    {"t":"14:25:04.720","ts_utc":"07:25:04.700Z","phase":"run","temp":40.8,"vx":0.6,"vy":0.2,"vz":0.9,"ax":18.0,"ay":12.0,"az":26.0}
  ],
  "controller": [
    {"t":"14:25:03.512","raw_2120":5,"running":true,"dir":"up","speed_mm_s":120,"floor":1},
    {"t":"14:25:05.010","raw_2120":5,"running":true,"dir":"up","speed_mm_s":1021,"floor":3},
    {"t":"14:25:09.740","raw_2120":1280,"running":false,"dir":null,"speed_mm_s":0,"floor":5}
  ]
}
```
`phase` phân biệt mẫu rung thuộc vùng pre-roll / run / post-roll. Tên file gồm thời điểm bắt đầu + hướng.

---

## 7. Trường hợp biên

- Khởi động khi car 1 **đang chạy** (`bit2=1` lúc start): đánh dấu `incomplete=true` (thiếu mốc bắt đầu thật + pre-roll).
- Phiên rác: lọc bằng `NGUONG_CHAY` và `MIN_DURATION` (1.5 s).
- Chế độ **bảo trì/lỗi** (`8480-1` bit3/bit5): gắn nhãn vào `flags` của phiên, vẫn ghi.
- Socket rớt giữa phiên: vẫn đóng phiên, `incomplete=true`, đánh dấu khoảng trống.
- Đổi hướng giữa chuyến (hiếm): ghi nhận trong `controller`, không tách phiên.

---

## 8. Tham số & quyết định đã chốt

| Mục | Giá trị |
|---|---|
| sn/level thang | `2026010900100098` / `2026010900100098` (Name "SmartCabin") |
| sn/level rung | `VS01` / `2026010900100098` |
| Cabin theo dõi | **car 1** (VS01 gắn ở đó); dùng key `8480-1`, `8473-1`, `8465-1`, `8448-1`, `8449-1` |
| Lấy thêm tầng + đếm chuyến | **Có** |
| Pre-roll / post-roll | **Bật**, mặc định `PRE_ROLL_S=2.0`, `POST_ROLL_S=2.0` |
| Định dạng lưu | **A** — JSON mỗi phiên + `index.csv` |
| `NGUONG_CHAY` | 20 mm/s |
| `MIN_DURATION` | 1.5 s |
| `T_DEBOUNCE` | 0.5–1.0 s (chốt lúc code) |
| Ghi lúc đứng yên | **Không** — chỉ ghi phiên chạy (kèm pre/post-roll) |
| URL / vận chuyển | `http://127.0.0.1:3000` (mặc định tại chỗ); hoặc `https://orangepi.local` từ máy khác |

---

## 9. Các bước hiện thực (sau khi review plan)

1. Tách phần nhận/giải mã Socket.IO trong `es_vs_01_ws.py` thành module tái dùng; hỗ trợ subscribe nhiều sn và giữ state theo sn.
2. Bộ giải mã rung (sẵn có) + bộ giải mã trạng thái thang từ `8480-1`/`8473-1`/`8465-1`/`8448-1`/`8449-1` → `{running, dir, speed, floor, run_number, flags}`.
3. Máy trạng thái phát hiện phiên (§3) + debounce + lọc `NGUONG_CHAY`/`MIN_DURATION`.
4. Vòng đệm pre-roll + ghi tiếp post-roll (§4.1).
5. Session Manager: mở/đóng phiên, gom mẫu rung + trạng thái, ghi JSON + cập nhật `index.csv` (§6).
6. Xử lý biên (§7) + log + tự kết nối lại.
7. Kiểm thử: mock `CONN_DATA` cho cả hai sn (bật/tắt bit2 của `8480-1`, đổi `8473-1`) kiểm tra cắt phiên, lọc rác, pre/post-roll đúng; rồi chạy thực tế vài chuyến car 1, đối chiếu số phiên với bộ đếm `8448/8449-1`.
```

---

## 10. Hiện thực & chạy bằng uv

Đã hiện thực (2026-11-10). Các file:

| File | Vai trò |
|---|---|
| `socketio_client.py` | Tầng truyền dùng chung: WebSocket (RFC 6455) + Engine.IO v4 + Socket.IO v5, lớp `SocketIOClient` subscribe nhiều thiết bị, gộp delta theo sn, tự kết nối lại. Chỉ stdlib. |
| `es_vs_01_ws.py` | Nhận rung VS01 qua Socket.IO (dùng lại `SocketIOClient`). |
| `elevator_session_logger.py` | Chương trình chính: cắt rung theo phiên chạy thang (car 1) theo plan này. |
| `es_vs_01.py` | (sẵn có) đọc cảm biến trực tiếp qua Modbus RTU. |

Project quản lý bằng **uv** (`pyproject.toml`), **không có dependency ngoài** (toàn bộ dùng thư viện chuẩn). Thiết lập một lần:

```bash
uv sync          # tạo .venv + uv.lock, dùng Python hệ thống, gần như tức thì (không có dep)
```

Chạy (entry points khai báo trong `[project.scripts]`):

```bash
uv run elevator-logger                 # cắt rung theo phiên chạy thang (car 1)
uv run elevator-logger --outdir data/sessions --pre-roll 2 --post-roll 2
uv run es-vs-01-ws                      # nhận rung qua Socket.IO iot_core
uv run es-vs-01                         # đọc cảm biến trực tiếp qua Modbus RTU

# từ máy khác trong LAN (orangepi.local có mDNS):
uv run elevator-logger --url https://orangepi.local --insecure

# hoặc chạy thẳng file:
uv run python elevator_session_logger.py
```

Tham số chính của `elevator-logger`: `--nguong-chay` (mm/s), `--min-duration` (s), `--pre-roll`/`--post-roll` (s), `--car`, `--outdir`, `--url`/`--insecure`.

### Điểm cần xác nhận khi có chuyến chạy thật
1. **Scale tốc độ `8473-1`**: giả định mm/s không chia. Khi thang chạy, giá trị nên cỡ ~1000–2500 mm/s; nếu lệch ×10 thì sửa trong `decode_elevator`.
2. **`NGUONG_CHAY=20 mm/s`**: tinh chỉnh sau khi quan sát mức releveling thực tế.
3. **`run_number`** ghi tại thời điểm mở phiên (số chuyến trước chuyến này).

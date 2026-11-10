# Đánh giá cảm biến ES-VS-01 cho bài toán PdM máy kéo thang máy

> Nguồn: `docs/ES-VS-01 Product Manual.pdf` (Vibration Motion Sensor ES-VS-01, RS485 Modbus RTU).
> Trích xuất bằng `tools/extract_pdf_text.py`. Cập nhật: 2026-09-21.
> Kết luận này là input cho Task 0.3 / 0.6 trong `docs/predictive-maintenance-plan.md`.

---

## 1. Kết luận

**ES-VS-01 là vibration transmitter (cảm biến xuất giá trị tổng hợp), không phải accelerometer thô.** Nó **không thể** làm cảm biến chính cho phát hiện sớm hỏng bạc đạn, và **không thể** hỗ trợ trip segmentation. Vai trò là **kênh phụ** — phương án **(b)** trong Task 0.6 hạng mục 2b. Cần bổ sung một accelerometer thô làm kênh chính.

Nhưng giá trị của kênh phụ này **phụ thuộc loại máy kéo**, và cao hơn đánh giá ban đầu của tôi với một trong hai trường hợp:

| | Máy **có hộp số** | Máy **gearless** |
|---|---|---|
| Nhiệt độ (0x0000) | Dùng tốt | Dùng tốt |
| Gia tốc (0x000A-0x000C) | **Kênh trend tần số cao dùng được** — nhạy hơn kênh vận tốc ~83 lần, thấy được lỗi từ 0.02 mm/s | Vô dụng — tần số quan tâm nằm dưới ngưỡng cắt 10 Hz |
| Vận tốc (0x0001-0x0003) | Chỉ để chấm ngưỡng ISO 20816 | Vô dụng |
| Kết luận | Kênh phụ có giá trị chẩn đoán thật | Gần như chỉ còn là cảm biến nhiệt |

Lý do ở §4.1bis. Hai điều kiện chưa xác minh có thể làm mất giá trị này: **ngưỡng deadband** (§7 mục 8) và **dải tần thực của đại lượng gia tốc** (§7 mục 9).

---

## 2. Thông số từ datasheet

| Hạng mục | Giá trị |
|---|---|
| Đầu ra | RS-485, Modbus RTU |
| Dải tần | **10-1600 Hz** hoặc **10-5000 Hz** (hai biến thể) |
| Số trục | 1 trục hoặc 3 trục (biến thể) |
| Vận tốc rung | 0-50 mm/s, phân giải 0.1 mm/s, sai số <1% @160 Hz/10 mm/s |
| Dịch chuyển | 0-5000 µm, phân giải 0.1 µm, sai số <1% @40 Hz/400 µm |
| Gia tốc | ±16 g, phân giải 0.1 m/s², sai số <1% @160 Hz |
| Nhiệt độ bề mặt | -40~+80 °C, phân giải **0.1 °C** |
| Baud rate | Thanh ghi 0x07D1: 0=2400, 1=4800, 2=9600, 3=19200, 4=38400, 5=57600, **6=115200**, 7=1200. Datasheet ghi mặc định xuất xưởng 9600 |
| Khung truyền | 8 bit data, 1 stop bit, no parity |
| Chu kỳ đo | Datasheet chỉ ghi "Real time" — **không có số cụ thể, phải đo thực tế** |
| Nguồn | 10-30 V DC, 0.3 W @24 V |
| Bảo vệ | IP67, vỏ inox |
| Cách gắn | **Ren (threaded)** hoặc nam châm |
| Địa chỉ thiết bị | Thanh ghi 0x07D0, 1~254, mặc định 1 |

## 3. Bảng thanh ghi dữ liệu đo (biến thể 3 trục)

Function code `0x03` / `0x04`. Mọi giá trị là số nguyên, **đã nhân 10** (chia 10 để lấy giá trị thực). Nhiệt độ là số có dấu, giá trị âm ở dạng bù hai (0xFFFF = -0.1 °C).

| Địa chỉ | PLC | Nội dung |
|---|---|---|
| 0x0000 | 40001 | Nhiệt độ |
| 0x0001 | 40002 | Vận tốc rung trục X |
| 0x0002 | 40003 | Vận tốc rung trục Y |
| 0x0003 | 40004 | Vận tốc rung trục Z |
| 0x0004 | 40005 | Dịch chuyển trục X |
| 0x0005 | 40006 | Dịch chuyển trục Y |
| 0x0006 | 40007 | Dịch chuyển trục Z |
| 0x0009 | 40010 | Số phiên bản |
| 0x000A | 40011 | Gia tốc trục X |
| 0x000B | 40012 | Gia tốc trục Y |
| 0x000C | 40013 | Gia tốc trục Z |

Ngoài ra có các thanh ghi hiệu chuẩn (0x0050-0x0083, hệ số A/B dạng float cho từng đại lượng và từng trục) và cấu hình (0x07D0 địa chỉ, 0x07D1 baud).

**Toàn bộ dữ liệu đo là 13 thanh ghi vô hướng. Không có thanh ghi waveform, không có phổ FFT, không có bộ đệm mẫu thô.** Đây là bằng chứng quyết định về phân lớp thiết bị.

---

## 4. Lý do không dùng được làm cảm biến chính

### 4.1 Băng thông truyền dẫn không cho phép truyền waveform

Giả sử muốn truyền mẫu thô để tự phân tích phổ trong dải 5 kHz:

- Tần số lấy mẫu cần thiết theo thông lệ phân tích phổ: `fs ≥ 2.56 × 5000 = 12 800 Hz` mỗi trục
- 3 trục × 12 800 mẫu/s × 2 byte = **76 800 byte/s**

Năng lực Modbus RTU (8N1 → 10 bit/byte, chưa trừ overhead khung và thời gian liên khung):

| Baud | Thông lượng lý thuyết | Thiếu so với yêu cầu |
|---|---|---|
| 9600 | 960 byte/s | **80 lần** |
| 115200 | 11 520 byte/s | **6.7 lần** |

Kể cả ở baud cao nhất thiết bị hỗ trợ, đường truyền vẫn thiếu gần 7 lần — và đó là con số lý thuyết chưa tính overhead. Thiết bị được thiết kế để xuất vô hướng, không phải để stream.

### 4.1bis Bản 10-5000 Hz: kênh gia tốc có giá trị thật, kênh vận tốc thì không

**Đã xác nhận thiết bị là bản 10-5000 Hz** (2026-09-21). Đây là bản tốt hơn trong hai biến thể, và khi tính lại thì nó làm **đổi một phần kết luận ban đầu của tôi** theo hướng có lợi.

Vấn đề của một số đọc RMS dải rộng là **RMS cộng theo bình phương**. Một thành phần lỗi nhỏ ở tần số cao cưỡi trên một nền lớn ở tần số thấp gần như biến mất. Nhưng mức độ biến mất phụ thuộc vào *đại lượng* đang đo, vì quan hệ `a = 2πf·v` đánh trọng số rất mạnh cho tần số cao.

Ví dụ máy kéo **có hộp số**: motor ~24 Hz, nền rung khoẻ 1.4 mm/s, lỗi bạc đạn sinh năng lượng ở ~2 kHz.

| Biên độ lỗi (mm/s) | Vận tốc tổng | Count vận tốc | Gia tốc tổng | Count gia tốc | Kết quả |
|---|---|---|---|---|---|
| 0.02 | 1.400 | 14 | 0.328 | 3 | chỉ gia tốc thấy |
| 0.05 | 1.401 | 14 | 0.663 | 7 | chỉ gia tốc thấy |
| 0.10 | 1.404 | 14 | 1.274 | 13 | chỉ gia tốc thấy |
| 0.20 | 1.414 | 14 | 2.522 | 25 | chỉ gia tốc thấy |
| 0.40 | 1.456 | 15 | 5.031 | 50 | cả hai thấy |

Nền khoẻ: vận tốc 14 count, gia tốc chỉ 2 count.

- **Thanh ghi vận tốc cần lỗi tới ~0.40 mm/s mới dịch một count.** Mù hoàn toàn với vùng 0.02-0.2 mm/s, tức đúng vùng "phát hiện sớm".
- **Thanh ghi gia tốc dịch ngay từ 0.02 mm/s**, và từ 2 count lên 7 count ở mức lỗi 0.05 mm/s. Nhạy hơn khoảng **83 lần** đúng bằng tỉ số `2000/24`.

Kết luận thực hành: với máy có hộp số, **thanh ghi gia tốc 0x000A-0x000C là kênh đáng dùng, không phải thanh ghi vận tốc 0x0001-0x0003**. Đây là điều tôi đánh giá thấp ở §4.2 và cần đính chính.

Ngược lại với máy **không hộp số**: puly 0.8 Hz, tần số lỗi bạc đạn ~5 Hz. Cả hai **nằm dưới ngưỡng cắt 10 Hz**, nên bị lọc bỏ trước khi tới bộ tính toán. Trọng số `2πf` không cứu được tần số đã bị lọc mất — và bản thân `2πf` ở 5 Hz cũng quá nhỏ. Với gearless, kết luận không đổi: thiết bị gần như không thấy gì ngoài nhiệt độ.

> Vì vậy **câu hỏi gearless hay có hộp số (Task 0.1) quyết định luôn giá trị của ES-VS-01**: máy có hộp số thì nó là kênh trend tần số cao dùng được; máy gearless thì nó chỉ còn là cảm biến nhiệt.

Số liệu trên tái lập được bằng:

```
python tools/es_vs_01_probe.py sensitivity --shaft-hz 24 --baseline-mm-s 1.4 --defect-hz 2000
python tools/es_vs_01_probe.py sensitivity --shaft-hz 0.8 --baseline-mm-s 1.4 --defect-hz 5
```

Toàn bộ phép tính được khoá bằng test trong `tools/test_es_vs_01_probe.py`.

**Một ẩn số quan trọng kèm theo:** datasheet ghi dải 10-5000 Hz cho sản phẩm, nhưng ghi sai số vận tốc "@160 Hz" và sai số gia tốc "@160 Hz". Thông lệ ISO 20816 đo vận tốc trong dải 10-1000 Hz, còn gia tốc mới dùng dải rộng. **Chưa biết dải nào áp cho đại lượng nào.** Nếu gia tốc cũng bị giới hạn ở 1000 Hz thì toàn bộ độ nhạy 2 kHz nói trên biến mất. Đây là hạng mục xác minh #9.

### 4.2 Phân giải vận tốc quá thô cho phát hiện sớm

Phân giải 0.1 mm/s trên dải 0-50 mm/s, tức tối đa 500 mức.

Máy kéo thang máy khoẻ thường nằm ở mức rung thấp, cỡ 1-2 mm/s RMS → chỉ **10-20 count**. Suy giảm bạc đạn giai đoạn đầu làm mức rung tổng dịch khoảng 0.1-0.3 mm/s → **1-3 count**.

Không thể xây baseline thống kê và phát hiện lệch chuẩn trên một tín hiệu chỉ có 1-3 count biên độ biến thiên; nhiễu lượng tử hóa sẽ lấn hết xu hướng. Thiết bị này chỉ phân giải được lỗi đã nặng (ví dụ mất cân bằng đưa mức rung từ 5 lên 15 mm/s — rất rõ).

### 4.3 Tần số cắt dưới 10 Hz loại bỏ đúng vùng tín hiệu cần tìm (máy gearless)

Với máy kéo **không hộp số**, thang 1.0 m/s puly 400 mm treo 1:1 → puly quay ~0.8 Hz. Tần số lỗi bạc đạn rơi vào vùng ~3-8 Hz, và thành phần 1x của trục là 0.8 Hz.

**Cả hai đều nằm dưới ngưỡng cắt 10 Hz của ES-VS-01.** Với máy gearless, thiết bị này không thấy chữ ký bạc đạn, và cũng không thấy được thành phần 1x để đánh giá mất cân bằng — chỉ còn đáp ứng kết cấu ở tần số cao rò qua.

Với máy **có hộp số** (motor ~24 Hz, BPFO ~100-200 Hz, gear mesh 1-3 kHz) thì các tần số này nằm trong dải, nên mức rung tổng có phản ứng — nhưng vẫn là con số gộp, không tách được thành phần nào đang tăng.

### 4.4 Chu kỳ cập nhật không đủ để phân đoạn chuyến — **đã đo: 1118 ms**

Datasheet chỉ ghi "Real time", không có số. Suy luận ban đầu của tài liệu này là cửa sổ tích lũy cỡ 1 s, dựa trên việc muốn tính RMS trong dải bắt đầu từ 10 Hz thì cần tối thiểu ~100 ms, và các thiết bị loại này thường lấy trung bình khoảng 1 giây.

**Phép đo ngày 2026-09-21 xác nhận: chu kỳ nội bộ là 1118 ms.** Chi tiết cách đo ở §6bis.5 — phát hiện qua một kênh không ngờ, là việc thiết bị ngừng trả lời bus theo chu kỳ đều đặn.

Một chuyến thang dài 3-10 s. Với chu kỳ 1118 ms, mỗi chuyến chỉ có **2.7 đến 8.9 giá trị thật sự mới**, và mỗi giá trị đã bị lấy trung bình trộn lẫn các pha nhả phanh, tăng tốc, chạy đều, giảm tốc, hãm phanh vào nhau. Transient nhả phanh chỉ dài vài chục ms nên biến mất hoàn toàn.

Không thể tách pha chuyến, tức không thực hiện được trip segmentation và regime conditioning — hai trụ kiến trúc ở §3.2 của kế hoạch chính. **Đây là lý do nghiêm trọng nhất**, độc lập với mọi vấn đề về phân giải hay băng thông, và giờ đã có số đo chứ không còn là suy luận.

---

## 5. Vai trò nên dùng ES-VS-01

### 5.1 Kênh nhiệt độ — giá trị cao, dùng ngay

Phân giải 0.1 °C trên dải làm việc thực tế 20-60 °C cho hàng trăm mức phân giải. Đây là tín hiệu **được phân giải tốt** và biến thiên chậm, hoàn toàn phù hợp với tốc độ cập nhật của Modbus.

Nhiệt độ phát hiện được: phanh kéo (brake drag), bạc đạn khô mỡ tiến tới bó, motor quá tải, suy giảm dầu hộp số. Chỉ số hữu ích nhất là **ΔT so với nhiệt độ môi trường** và **tốc độ tăng nhiệt theo số chuyến**, không phải nhiệt độ tuyệt đối.

### 5.2 Gia tốc dải rộng — kênh trend thật, nhưng chỉ với máy có hộp số

Sau phân tích ở §4.1bis, đây là vai trò được nâng cấp so với đánh giá ban đầu.

Với máy **có hộp số**, thanh ghi gia tốc `0x000A-0x000C` nhạy với năng lượng tần số cao ở mức đủ để làm **kênh theo dõi xu hướng suy giảm cơ khí**: nền khoẻ ~2 count, một lỗi bạc đạn 0.05 mm/s ở 2 kHz đẩy lên 7 count. Đó là biến động rõ ràng, đo được.

Giới hạn cần nói rõ: nó cho biết **"năng lượng tần số cao đang tăng"** nhưng không cho biết **"tăng ở tần số nào"**, nên không phân biệt được bạc đạn với bánh răng, với điện, với lỏng kết cấu. Muốn phân nhóm nghi vấn vẫn phải có phổ từ accelerometer thô. Và nó không giải quyết được vấn đề trip segmentation ở §4.4 — đó là rào cản độc lập.

Với máy **gearless**, kênh này vô dụng vì tần số quan tâm nằm dưới ngưỡng cắt 10 Hz.

### 5.3 Mức rung tổng dạng vận tốc — chỉ để chấm ngưỡng ISO

Thanh ghi vận tốc `0x0001-0x0003` cần lỗi tới ~0.4 mm/s mới dịch một count, nên chỉ dùng được cho ngưỡng độ nặng kiểu ISO 20816: mất cân bằng lớn, lỏng bulong bệ, lệch trục nặng. **Không** dùng làm cơ sở phát hiện sớm. Giữ lại vì nó là đại lượng mà tiêu chuẩn và kỹ thuật viên quen đọc.

### 5.4 Cơ hội: giám sát phân tầng cho cả đội thang

Đây là cách biến số cảm biến đã có thành tài sản thay vì chi phí chìm. ES-VS-01 có IP67, vỏ inox, gắn ren, nguồn 10-30 V, 0.3 W — rất phù hợp lắp đại trà.

| | Mức 1 — phủ rộng | Mức 2 — chẩn đoán sâu |
|---|---|---|
| Cảm biến | ES-VS-01 (Modbus) | Accelerometer thô + ES-VS-01 làm kênh nhiệt |
| Đối tượng | Thang thường, phủ toàn toà nhà | Thang pilot, thang quan trọng, thang đã có dấu hiệu |
| Năng lực | Nhiệt độ, mức rung tổng, ngưỡng ISO | Trip segmentation, envelope spectrum, phát hiện sớm bạc đạn, health index |
| Chi phí/điểm | Thấp | Cao hơn |

Mức 1 cũng đóng vai trò sàng lọc: thang nào vượt ngưỡng thô thì nâng cấp lên mức 2 để chẩn đoán.

---

## 6. Cần bổ sung: accelerometer thô làm kênh chính

Lựa chọn cuối cùng phụ thuộc kết quả Task 0.1 (gearless hay có hộp số) và Task 0.6 (nghiên cứu decimation). Các ứng viên:

| Ứng viên | Giao tiếp | Đặc điểm | Phù hợp |
|---|---|---|---|
| IIS3DWB (ST) | SPI số | ±16 g, đáp ứng phẳng tới ~6.3 kHz, ODR ~26 kHz, thiết kế cho condition monitoring | Máy có hộp số; một chip, không cần ADC ngoài |
| ADXL355 (ADI) | SPI số | ±2/4/8 g, nhiễu thấp ~25 µg/√Hz, ODR tới 4 kHz, ổn định tần số thấp tốt | Máy gearless, vùng vài Hz |
| ADXL1002 (ADI) | Analog | ±50 g, băng thông tới ~11 kHz | Dải rộng, cần ADC ngoài |
| Piezo IEPE + ADC 24-bit | Analog | Hiệu năng tốt nhất, dải động lớn | Khi cần độ nhạy cao nhất, chi phí cao nhất |

**Cách gắn cho cảm biến chính: ren hoặc keo, không dùng nam châm.** Gắn nam châm thường chỉ đáng tin tới vài kHz do cộng hưởng của cụm nam châm–cảm biến–bề mặt; nếu cần bắt gear mesh ở vài kHz thì nam châm sẽ làm suy giảm đúng vùng quan trọng mà không hiển thị lỗi gì. Xem Task 0.4.

---

## 6bis. Kết quả đo thực tế trên bàn — 2026-09-21

Thiết bị: 1 bộ ES-VS-01, nối qua bộ chuyển USB-RS485 FTDI FT232 trên `COM7`.
Công cụ: `tools/es_vs_01_probe.py` (khung Modbus RTU tự viết, đã kiểm chứng CRC với ví dụ trong datasheet qua `tools/test_es_vs_01_probe.py`, 22/22 check đạt).

### 6bis.1 Nhận dạng thiết bị

| Hạng mục | Kết quả | Ghi chú |
|---|---|---|
| Baud thực tế | **115200** | Quét từ 9600 xuống, chỉ trả lời ở 115200 |
| Thanh ghi baud 0x07D1 | **6** | Khớp mã 6 = 115200 trong datasheet |
| Địa chỉ Modbus 0x07D0 | 1 | Mặc định |
| Biến thể | **3 trục** | Đọc 0x000A-0x000C thành công, không có exception |
| Thanh ghi version 0x0009 | **2314** (0x090A) | Chưa rõ cách giải nghĩa |
| Nhiệt độ | 30.7-30.8 °C | Hợp lý với nhiệt độ phòng |

> **Đính chính so với thông tin ban đầu:** thiết bị **có** hỗ trợ 115200, không giới hạn ở 2400/4800/9600, và đang chạy sẵn ở 115200. Đây là tin tốt cho vai trò kênh trend — tốc độ polling cao hơn nhiều so với giả định.

### 6bis.2 Hành vi ở trạng thái tĩnh — 28 s, 348 mẫu

| Trường | min | max | Số mức lượng tử | Số lần đổi giá trị |
|---|---|---|---|---|
| speed_x / y / z | 0.0 | 0.0 | **1** | **0** |
| pos_x / y / z | 0.0 | 0.0 | **1** | **0** |
| accel_x / y / z | 0.0 | 0.0 | **1** | **0** |
| temperature | 30.7 | 30.8 | 2 | 1 |

**Cả 9 trường rung đọc đúng 0.0 suốt 28 giây, không đổi một lần nào.** Cảm biến đặt trên mặt bàn có rung nền của toà nhà và quạt laptop, nhưng đầu ra vẫn là zero tuyệt đối — tức thiết bị có **deadband**, không chỉ đơn thuần là lượng tử hóa thô.

Đây là phát hiện mới, quan trọng hơn cả vấn đề phân giải đã nêu ở §4.2: nếu deadband ở mức đáng kể, một máy kéo khoẻ ở 1-2 mm/s sẽ nằm ngay sát sàn đo và gần như không còn dải động hữu dụng để xây baseline. **Cần đo ngưỡng deadground** (xem §7 hạng mục 8).

### 6bis.3 Tốc độ polling — ba con số khác nhau, đừng lẫn

Đọc 13 thanh ghi ở 115200 baud.

| Con số | Giá trị | Nghĩa |
|---|---|---|
| Tốc độ của file CSV đã thu | **12.4 poll/s** | 348 mẫu / 28.0 s. Thấp vì timeout đặt 0.35 s nên mỗi lần thiết bị ngừng trả lời là mất 0.35 s × 4 lần |
| Tốc độ tốt nhất đạt được | **19.3 poll/s thành công** | 676/748 giao dịch trong 35 s, timeout 0.20 s, gap 2 ms |
| Trần theo độ trễ giao dịch | **~36 poll/s** | Từ p50 = 27.8 ms |

Cấu thành của 27.8 ms mỗi giao dịch:

| Thành phần | Thời gian | Tỉ lệ |
|---|---|---|
| Bit thật trên đường truyền (39 byte @115200) | 3.4 ms | **12%** |
| FTDI LatencyTimer | ~16 ms | 58% |
| Turnaround thiết bị + lịch trình Windows | ~8 ms | 30% |

Độ trễ chi tiết: min 11.8 ms, p50 27.8 ms, p90 61.9 ms, p99 78.5 ms, max 84.1 ms.

**Nguyên nhân overhead đã xác định: FTDI LatencyTimer = 0x10 = 16 ms** (đọc từ `HKLM\SYSTEM\CurrentControlSet\Enum\FTDIBUS\...\Device Parameters`), là mặc định của driver FTDI — không phải lỗi cảm biến, không phải chất lượng đường truyền.

Hệ quả: **baud không phải yếu tố giới hạn.** Ở 115200 chỉ 12% thời gian giao dịch là truyền bit thật. Tăng baud thêm vô nghĩa.

> **Nhưng cả ba con số trên đều không quan trọng bằng một điều:** thiết bị chỉ sinh giá trị mới mỗi **1118 ms** (§6bis.5). Poll ở 19/s nghĩa là đọc lại cùng một giá trị khoảng 21 lần. Ràng buộc thật nằm ở cảm biến, chậm hơn năng lực polling ~21 lần.

### 6bis.4 Hệ quả thiết kế cho Task 18 (adapter Modbus tại edge)

Vì cảm biến chỉ cập nhật mỗi 1118 ms, mục tiêu polling hợp lý là **2-3 lần/giây** (đủ theo Nyquist cho chu kỳ 1.12 s), không phải 36. Điều này nới lỏng đáng kể ngân sách bus RS485:

| Tình huống | Thời gian/giao dịch | Số thiết bị trên một bus (poll 2 Hz mỗi thiết bị) |
|---|---|---|
| Host hiện tại, LatencyTimer 16 ms | 28 ms | ~17 |
| Gateway Linux, latency 3-5 ms | ~8 ms | ~60 |

Hai điều cần đưa vào thiết kế adapter:

1. **Timeout phải lớn hơn đuôi p99, có dự phòng.** Đo được p99 = 78.5 ms, max 84.1 ms. Thử ở timeout 0.06 s cho tỉ lệ lỗi giả 37% chỉ vì nhiều phản hồi chậm hợp lệ bị đếm thành lỗi. Đề xuất tối thiểu 200 ms.
2. **Phải xử lý được việc slave im lặng theo chu kỳ.** Đây là hành vi bình thường của thiết bị, không phải sự cố — adapter không được coi đó là mất kết nối và không được sinh cảnh báo thiết bị lỗi.

### 6bis.5 Chu kỳ cập nhật nội bộ = 1118 ms — đo được mà không cần rung

Đây là phát hiện quan trọng nhất của buổi đo, và nó đến từ một kênh không ngờ.

Ban đầu tỉ lệ lỗi ~12% bị nghi là do đường truyền. Nhưng khi quét khoảng nghỉ giữa các giao dịch từ 1.75 ms lên 40 ms, **số lỗi gần như không đổi** (22, 22, 22, 22, 18) trong khi số giao dịch giảm từ 167 xuống 96. Lỗi tỉ lệ với *thời gian*, không tỉ lệ với *số giao dịch* — nên không thể là mất khung ngẫu nhiên hay turnaround của adapter.

Phân tích tiếp: lỗi đi thành **chùm liên tiếp**, mỗi lần timeout lại thử ngay và lại timeout. Phải gộp chùm trước khi đo khoảng lặp, nếu không con số thu được chỉ là tiếng vang của chính giá trị timeout. Sau khi gộp:

| Timeout | Số chùm | Khoảng giữa các chùm (median) | stdev/median |
|---|---|---|---|
| 0.12 s | 37 | **1118 ms** | 0.02 |
| 0.20 s | 31 | **1118 ms** | 0.01 |
| 0.06 s | 68 | 527 ms (không tin được) | 0.49 |

**Hai cấu hình timeout độc lập cho cùng một giá trị 1118 ms, khớp tới từng ms, độ đều 1-2% qua hơn 30 chu kỳ.** Nếu con số này là artifact của timeout thì hai lần đo phải khác nhau. Bản 0.06 s không dùng được vì timeout thấp hơn đuôi p90 (62 ms) nên đếm nhầm phản hồi chậm hợp lệ thành lỗi.

Mỗi chùm gồm 2-4 lần timeout liên tiếp, tức cửa sổ im lặng dài khoảng 250-490 ms trong mỗi chu kỳ 1118 ms.

Diễn giải: thiết bị **ngừng phục vụ bus trong lúc chạy chu kỳ đo/tính toán nội bộ**. Từ bên ngoài, đó chính là chữ ký của chu kỳ cập nhật.

Điều này **khớp với dự đoán ~1 s** mà §4.4 đưa ra từ nguyên lý trước khi có phép đo.

Tái lập bằng:

```
python tools/es_vs_01_probe.py bench --port COM7 --baud 115200 --unit 1 \
    --seconds 40 --registers 13 --gap-ms 2 --timeout 0.12
```

Logic gộp chùm được khoá bằng test trong `tools/test_es_vs_01_probe.py` (có test chứng minh nếu *không* gộp thì kết luận sẽ sai thành 122 ms).

**Mức độ tin cậy:** đây là suy luận *gián tiếp*. Cửa sổ im lặng về nguyên tắc có thể do việc khác (housekeeping, watchdog, ghi EEPROM). Nhưng độ đều 1-2% qua 37 chu kỳ và giá trị đúng bằng thang thời gian kỳ vọng của một cửa sổ RMS cho dải từ 10 Hz làm cho cách giải thích này rất vững. Vẫn nên đối chiếu với lệnh `rate` khi có rung thật — nếu hai phép đo khớp thì coi như chốt.

### 6bis.6 Chưa đo được — cần rung thật

Các hạng mục sau bắt buộc phải có kích thích rung, không làm được bằng cách để cảm biến nằm yên:

- Chu kỳ cập nhật nội bộ thực
- Step response và cửa sổ lấy trung bình
- Ngưỡng deadband
- Suy giảm ở vùng tần số thấp
- Tương quan với accelerometer tham chiếu

---

## 7. Việc cần xác minh trên thiết bị thật (Task 0.3)

| # | Việc | Trạng thái | Vì sao quan trọng |
|---|---|---|---|
| 1 | **Biến thể dải tần** | **Xong** | Là bản **10-5000 Hz**. Kéo theo kết luận mới ở §4.1bis: kênh gia tốc có giá trị thật với máy có hộp số |
| 2 | **Baud tối đa thực tế** | **Xong** | Chạy 115200 (mã 6). Nhưng overhead host 16 ms khiến baud không còn là yếu tố giới hạn — xem §6bis.3 |
| 3 | **Chu kỳ cập nhật thực** | **Đo được gián tiếp: 1118 ms** | Xác định qua chu kỳ thiết bị ngừng trả lời bus (§6bis.5), hai timeout độc lập cho cùng kết quả. Vẫn nên đối chiếu bằng `rate` khi có rung |
| 4 | **Số điểm dữ liệu thực trên một chuyến** | **Suy ra: 2.7-8.9** | Từ chu kỳ 1118 ms và chuyến 3-10 s. Xác nhận trực tiếp việc không hỗ trợ trip segmentation |
| 5 | **Đáp ứng thực ở vùng tần số thấp** | **Chờ rung** | Xác nhận ngưỡng cắt 10 Hz và mức suy giảm dưới ngưỡng |
| 6 | **Tương quan với accelerometer tham chiếu** | **Chờ bộ đo** | Định lượng phần thông tin ES-VS-01 còn giữ được |
| 7 | **Ren gắn có khớp bề mặt máy kéo pilot không** | **Chờ hiện trường** | Ưu tiên gắn ren; nếu không khoan được thì dùng nam châm và ghi nhận giới hạn tần số |
| 8 | **Ngưỡng deadband** — mức rung tối thiểu để giá trị rời khỏi 0.0 | **Chờ rung** | Phát hiện từ §6bis.2: ở trạng thái tĩnh đầu ra là zero tuyệt đối. Nếu deadband cao, máy khoẻ ở 1-2 mm/s gần như không còn dải động để xây baseline |
| 9 | **Dải tần nào áp cho đại lượng nào**: gia tốc có thật sự đo tới 5000 Hz, hay cũng bị giới hạn 1000 Hz như thông lệ ISO cho vận tốc? | **Chờ rung** (mục mới) | **Quyết định toàn bộ giá trị của kênh gia tốc.** Nếu gia tốc cũng chỉ tới 1000 Hz thì độ nhạy 2 kHz ở §4.1bis biến mất. Cách đo: kích thích ở các tần số biết trước (200 Hz, 800 Hz, 2 kHz, 4 kHz) và xem thanh ghi nào phản ứng |

### 7.1 Quy trình đo phần còn lại

Ba hạng mục 3, 4, 8 làm được ngay trên bàn, chỉ cần một nguồn rung. Không cần thang máy.

```
# 1. Đo chu kỳ cập nhật — cần rung LIÊN TỤC và BIẾN ĐỔI suốt thời gian đo
python tools/es_vs_01_probe.py rate --port COM7 --baud 115200 --unit 1 --seconds 60

# 2. Step response — theo đúng mốc thời gian mà lệnh in ra
python tools/es_vs_01_probe.py log --port COM7 --baud 115200 --unit 1 \
    --seconds 120 --out data/es-vs-01/bench-step.csv
python tools/es_vs_01_probe.py analyze data/es-vs-01/bench-step.csv
```

Kịch bản step response, quan trọng nhất vì nó bộc lộ cửa sổ lấy trung bình:

| Thời gian | Việc làm | Thông tin thu được |
|---|---|---|
| 0-20 s | Để yên hoàn toàn | Nền tĩnh, xác nhận deadband |
| 20-60 s | Rung đều, duy trì (đặt lên vỏ quạt đang chạy là tiện nhất) | Giá trị ổn định ở mức nào |
| 60-80 s | Ngừng rung, để yên | **Thời gian suy giảm về 0 chính là cửa sổ lấy trung bình.** Nếu là trung bình trượt 1 s thì thấy dốc xuống ~1 s |
| 80-120 s | Gõ nhẹ từng nhịp, có khoảng nghỉ rõ giữa các nhịp | Độ trễ phản hồi, và liệu xung ngắn có bị bỏ qua |

Để đo **deadband** (hạng mục 8): tăng cường độ rung từ rất nhẹ lên dần, ghi nhận mức nào giá trị bắt đầu rời khỏi 0.0. Không cần tuyệt đối chính xác — chỉ cần biết nó ở cỡ 0.1 mm/s hay cỡ 1 mm/s, vì hai trường hợp đó dẫn tới kết luận rất khác nhau.

---

## 8. Ảnh hưởng tới BOM

Kết luận phương án (b) nghĩa là mỗi điểm giám sát **mức 2** cần cả hai: ES-VS-01 (nhiệt + rung tổng, đã có) và một accelerometer thô mới (kênh chính). Chi phí mỗi điểm tăng so với giả định ban đầu chỉ dùng cảm biến sẵn có.

Bù lại, ES-VS-01 không bị loại bỏ: nó vẫn là cảm biến nhiệt tốt cho mức 2, và là cảm biến chính cho mức 1 phủ rộng. Task 0.6 chốt BOM chi tiết cho cả hai mức kèm giá.

# Người hỗ trợ kiểm hộp trước khi hộp lên SM

Trang này dành cho admin. Người dùng thường không phải làm gì thêm: họ tạo và duyệt hộp như cũ.

## Ai làm gì

1. SC tạo hộp trong ứng dụng và duyệt.
2. Script của bảng tính điền món của hộp vào bảng (như trước, chỉ vào ô trống).
3. SC tick ô **cần trong ca này**: ô vuông ở dòng tiêu đề của khối (cột C, J, Q cho Box B2, B3, A1).
4. Người hỗ trợ ra kệ kiểm ID và số lượng thật. Món nào thiếu thì xoá ô ID của món đó. Kiểm xong thì tick ô **đã kiểm**: ô vuông ở dòng ngay dưới món cuối của khối (cột D, K, R).
5. Script của bảng tính báo hai ô tick và danh sách món còn lại về ứng dụng (ngay sau mỗi lần điền, và mỗi 30 phút).
6. Khi công tắc **Chờ người hỗ trợ kiểm trước khi lên SM** bật, ứng dụng chỉ đưa lên SM hộp có đủ hai tick, và đưa đúng danh sách của người hỗ trợ.

## Ứng dụng xử lý thế nào

- **Món bị xoá (thiếu)** và **số lượng giảm**: nhận luôn. Hộp trong ứng dụng đổi theo danh sách của người hỗ trợ. Món thiếu vào danh sách "đã dùng trong bảng tính" để không ai chọn lại, cho đến khi quy tắc dùng lại món cũ trả nó về.
- **ID lạ** (không có trong hộp) hoặc **số lượng tăng**: hộp bị giữ lại và hiện chỗ khác. Người làm hộp hoặc người hỗ trợ sửa cho khớp.
- **Hết món**: hộp bị giữ lại.
- **Tick có từ hôm trước** không tính. Ứng dụng chỉ nhận tick của người hỗ trợ sau khi đã thấy ô đó chưa tick, rồi mới thấy tick.
- Hộp chưa đủ điều kiện hiện cho người làm hộp một câu ngắn ("chờ người hỗ trợ kiểm"). Không có gì kỹ thuật.

## Cài đặt (làm một lần, chỉ admin)

1. Trong Render đặt biến `SHEET_TOKEN`: một chuỗi dài ngẫu nhiên, khác `SYNC_TOKEN`. Biến này chỉ cho phép ghi nhận kết quả kiểm, không làm được việc khác.
2. Tab **Tự động hoá**, khung **Bảng tính Google**: dán link bảng tính (bản sao để thử trước, rồi bảng thật). Link chỉ lưu trong cơ sở dữ liệu, không bao giờ vào kho mã.
3. Mở bảng tính, Tiện ích mở rộng, Apps Script: dán bản script mới (nút **Sao chép script bảng tính**), lưu, tải lại bảng.
4. Menu **Box builder**, **Đặt mã Box builder**: gõ `SHEET_TOKEN` một lần. Mã nằm trong thuộc tính của script, không nằm trong file.
5. Menu **Gửi kết quả kiểm cho Box builder (xem trước)**: đọc dòng báo, rồi xác nhận. Bật chạy tự động mỗi 30 phút nếu muốn.
6. Chạy thử một ngày với công tắc **Chờ người hỗ trợ kiểm trước khi lên SM** tắt (chỉ xem trạng thái), rồi mới bật.

## Giới hạn cần biết

- Script chỉ đọc hai ô tick và các ô ID, SL của khối. Nó vẫn chỉ ghi vào ô trống khi điền.
- Một khối nằm sát khối sau (một số tab, ví dụ M5 từ mục thứ sáu) có thể không có ô tick của người hỗ trợ. Khi đó script báo "chưa biết" và hộp chờ.
- Script gửi báo cáo mỗi 30 phút kể cả khi không có gì đổi. Việc này cũng làm mới giờ "báo lần cuối" ở khung Bảng tính Google.
- Sau khi khôi phục cơ sở dữ liệu từ bản sao lưu, kết quả kiểm cũ không còn. Script gửi lại ở lần chạy kế tiếp.

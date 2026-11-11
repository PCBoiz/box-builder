# Buổi thử chính thức: làm gì trước, xem gì khi thử

Trang này ngắn và dành cho chủ dự án. Các bước đặt biến nằm trong `docs/LOGIN.md`.

## Trước khi thử (làm một lần trong Render)

1. Đặt `ADMIN_IDS` (ID admin), `USER_IDS` (ID người dùng tạm) và `USER_PASSWORD` (mật khẩu chung). Lưu, chờ khoảng một phút.
2. Mở trang, đăng nhập bằng ID admin. Vào tab **Tự động hoá**: dán danh sách người dùng ("ID, tên, module") và link bảng tính.
3. Nếu có biến `BOX_DONE_DAYS`, để 1.
4. Kiểm tra nhanh: mở `/dung` bằng ID người dùng, tạo một hộp, duyệt, rồi bỏ duyệt. Xong thì xoá hộp.

## Thử với một người, rồi cả nhóm

- Người đầu tiên làm cả ngày: kế hoạch hôm nay, B2, B3, A1 cho một unit, duyệt, chạy script của nhóm để đưa lên SM (thử MỘT hộp trước, chỉ bạn đăng nhập SM được).
- Rồi mới mời cả nhóm. Mỗi người tạo hộp của mình và tự duyệt.
- Trang admin chỉ để xem: bảng "Tiến độ các hộp" cho biết ai có mấy hộp và chỗ nào vướng.

## Hai điều cần để ý khi nhiều người cùng làm

- **Hàng chờ chọn đồ.** Mặc định chỉ chọn đồ cho một hộp mỗi lúc (`MAX_PARALLEL_LLM=1`) để không chọn trùng. Khi cả nhóm bấm tạo cùng lúc (ví dụ đầu ca B2), hộp phải xếp hàng và người cuối chờ lâu. Nếu thấy chờ quá lâu, đặt `MAX_PARALLEL_LLM=2` hoặc 3 trong Render. Ứng dụng vẫn không cho một món nằm ở hai hộp, chỉ có thể tốn thêm một lượt chọn lại khi hai hộp tranh nhau.
- **Kho.** Kho thật chỉ còn khoảng 400 món trống (phần còn lại đang nằm trong hộp SM). Một hộp B2 hoặc B3 cần 21 món, A1 cần 12. Khi kho gần hết, trang người dùng báo "Kho còn N món trống" ở bước 1. Món trong hộp SM chỉ trống lại khi hộp đó được trả trong SM, và món của hộp trong ứng dụng về kho sau 1 ngày.

## Đã diễn tập trước (máy chủ thử, không phải trang thật)

12 người dùng tạo 6 hộp mỗi người cùng lúc (72 hộp): tất cả đủ món, không món nào nằm ở hai hộp, duyệt hết được, thăm dò của trang mất dưới 0,1 giây, tạo hộp chậm nhất khoảng 1,7 giây. Máy chủ thử dùng bộ chọn giả và cơ sở dữ liệu cục bộ, nên chưa nói được tốc độ của AI thật hay của Neon.

## Khi có lỗi

- Ghi lại: ai (ID), lúc mấy giờ, hộp nào (module, loại hộp), câu thông báo trên màn hình.
- Admin vào tab **Tự động hoá**, mục "Việc gần đây" xem ai đã tạo, duyệt, bỏ duyệt hay xoá hộp nào.
- Quay lại bản cũ trên Render: xem `docs/LOGIN.md`, mục "Quay lại bản cũ".

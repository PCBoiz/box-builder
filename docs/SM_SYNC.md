# Đồng bộ kho từ SM sang Box builder

Công cụ này là một script Tampermonkey chạy trong chính tab SM của bạn. Nó **chỉ đọc** kho đồ từ SM rồi gửi cho Box builder.
Nó không tạo, sửa hay xoá gì trong SM, và máy chủ Box builder không bao giờ kết nối tới SM.

## Cài đặt (Firefox + Tampermonkey)

1. Bấm biểu tượng Tampermonkey, chọn **Create a new script**.
2. Xoá toàn bộ nội dung mẫu, dán toàn bộ file `integrations/box_builder_sm.user.js`, nhấn Ctrl+S.
3. Nếu đang có script đồng bộ cũ (tải từ GitHub bằng loader), tắt nó đi để hai nút không chồng nhau.
4. Mở https://sm.config.inc/boxes và đăng nhập SM như thường lệ. Góc dưới bên phải có nút **Box builder**.
5. Bấm nút đó, nhập:
   - **Địa chỉ Box builder**, ví dụ `https://box-builder-guwc.onrender.com`
   - **Mã đồng bộ**: giá trị `SYNC_TOKEN` (trong Render, mục Environment của dịch vụ box-builder)
   - **Khu**: `Hanoi-GPI`
6. Bấm **Kiểm tra kết nối**. Khi cả ba bước báo xong, bấm **Đồng bộ kho ngay**.

Lần đầu, Tampermonkey hỏi có cho script gọi tới địa chỉ Box builder không. Chọn cho phép (Always allow domain).

## Thông báo và cách xử lý

| Thông báo | Nghĩa là | Cách xử lý |
|---|---|---|
| Chưa nhập địa chỉ Box builder | Ô địa chỉ trống | Nhập địa chỉ bắt đầu bằng https:// |
| Chưa đăng nhập SM trong tab này | SM đã đăng xuất | Đăng nhập SM, bấm lại |
| Không kết nối được tới ... | Sai địa chỉ, mất mạng, hoặc máy chủ miễn phí đang ngủ | Kiểm tra địa chỉ, đợi 1 phút, bấm lại |
| Mã đồng bộ sai | `SYNC_TOKEN` nhập chưa đúng | Lấy đúng giá trị trong Render |
| Máy chủ Box builder chưa đặt SYNC_TOKEN | Render chưa có biến này | Thêm `SYNC_TOKEN` trong Render rồi deploy lại |
| Phiên bản Box builder này còn cũ | Máy chủ chưa có phần kiểm tra | Cập nhật Box builder (push bản mới) |
| ... chưa tự bù từ file CSV | SM không gửi tên tiếng Việt và máy chủ chưa biết bù | Cập nhật Box builder rồi thử lại |
| Chỉ N món dùng được ... Kho không bị thay đổi | Danh sách gửi lên gần như trống nên Box builder từ chối | Đọc phần "Thiếu tên tiếng Việt / hết hàng / khác khu" trong thông báo |
| Chỉ đọc được N món thuộc khu ... | Sai tên khu | Gõ đúng, ví dụ `Hanoi-GPI` |

## Điều cần biết

- SM không trả tên tiếng Việt cho món. Box builder lấy tên tiếng Việt từ file CSV của nó (theo Object ID). Món mới chưa có trong CSV và chưa có tên tiếng Việt sẽ bị bỏ qua cho đến khi file CSV được cập nhật.
- Chỉ món thuộc khu đã chọn, loại Obj, đang hoạt động và còn hàng mới được dùng.
- Box builder từ chối một lần đồng bộ nếu nó làm kho tụt dưới một nửa hoặc dưới 50 món, để một lần đọc lỗi không xoá mất kho.
- Link ảnh của SM là link ký sẵn nên hết hạn sau một thời gian. Đồng bộ lại thì ảnh hiện lại.
- Mã đồng bộ được lưu trong bộ nhớ riêng của Tampermonkey trên máy bạn, không nằm trong file script.

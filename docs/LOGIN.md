# Đăng nhập bằng ID công ty

Box builder có hai trang:

- `/dung` là trang của người làm hộp, và là trang chính của sản phẩm. Mỗi người đăng nhập bằng ID công ty, rồi tạo hộp, xem các món, đổi món, **tự duyệt hộp của mình**, sao chép danh sách và in phiếu lấy đồ. Mỗi người chỉ thấy hộp của chính mình.
- `/` là trang admin: xem quy trình có chỗ nào vướng không (bảng "Tiến độ các hộp"), xem kho, bảng tự động hoá, danh sách người dùng. Admin không phải là người duyệt; admin chỉ can thiệp khi cần (bỏ duyệt, xoá hộp, sửa kho).

Hộp đã duyệt là hộp sẵn sàng để script của nhóm đưa lên SM. Trước khi hộp lên SM, người làm bấm **Bỏ duyệt** để sửa lại. Hộp đã lên SM thì không bỏ duyệt được ở trang này.

Đăng nhập cho biết ai làm hộp nào và giữ người ngoài khỏi trang admin. **Ai biết một ID thì đăng nhập được bằng ID đó**, nên đừng gửi ID ra ngoài công ty. ID admin có thể thêm mã admin (bước 2).

## Bật đăng nhập (làm một lần, trong Render)

Claude và ứng dụng không ghi vào Render. Bạn tự làm các bước này.

1. Vào Render, dịch vụ box-builder, mục **Environment**. Thêm biến `ADMIN_IDS` với ID của các admin, cách nhau bằng dấu phẩy, ví dụ `1111,2222`. Một ID gồm 2 đến 20 ký tự: chữ, số, dấu gạch ngang, dấu gạch dưới hoặc dấu chấm. Không có dấu ngoặc kép.
2. Tuỳ chọn: thêm biến `ADMIN_CODE` với một mã chỉ admin biết. Khi có mã này, admin phải gõ thêm mã lúc đăng nhập. Người dùng thường không cần mã.
3. Lưu. Render chạy lại ứng dụng, mất khoảng một phút.
4. Mở trang, đăng nhập bằng ID admin (và mã nếu có).
5. Vào tab **Tự động hoá**, khung **Người dùng và quyền**. Dán danh sách người dùng, mỗi dòng một người: `ID, tên, module`. Thêm chữ `admin` ở cuối dòng nếu người đó là admin. Ví dụ:
   ```
   1111, Hải, M2
   2222, Hưng, M3, admin
   ```
6. Gửi mọi người link `/dung` của trang.

Khi chưa đặt `ADMIN_IDS`, ứng dụng mở như trước: ai có link cũng vào được cả hai trang. Vì vậy đẩy bản mới lên không làm admin bị khoá. Thanh đầu trang hiện "Đăng nhập chưa bật" cho đến khi bạn đặt biến. Không còn mã truy cập chung `ACCESS_CODE`: ứng dụng bỏ qua biến này.

## Những gì không đổi

Các công cụ chạy trong tab trình duyệt của nhóm không đăng nhập được, nên các đường dẫn sau vẫn mở như cũ và giữ lớp bảo vệ riêng của chúng (mã đồng bộ `SYNC_TOKEN` cho các việc ghi):

- script Tampermonkey của nhóm (v2.23.0) và công cụ đồng bộ của Box builder: đọc hộp đã duyệt `/api/taskboxes`, gửi kho `/api/catalog/sync`, báo hộp đã lên SM `/api/boxes/<mã>/pushed`, gửi hộp trong SM `/api/sm/boxes/sync`, kiểm tra kết nối `/api/sync/check`;
- script bảng tính Google: `/api/sheet-boxes`;
- trang cài công cụ: `/box_builder_sm.user.js`, `/box_builder_sm.txt`.

Mọi đường dẫn khác mặc định chỉ dành cho admin, kể cả đường dẫn thêm sau này, cho đến khi được ghi rõ là dành cho người dùng hoặc mở. Một bài test kiểm kê toàn bộ đường dẫn để giữ điều đó.

## Cách đăng nhập hoạt động

- Danh sách ID do admin giữ. ID không có trong danh sách (và không phải admin trong `ADMIN_IDS`) không vào được.
- Phiên đăng nhập lưu trong cơ sở dữ liệu, chỉ lưu bản băm của mã phiên, không cần thêm biến bí mật. Cookie chỉ gửi trong trang này (`HttpOnly`, `SameSite=Lax`, `Secure` khi dùng HTTPS). Phiên hết sau 14 ngày không dùng, hoặc 30 ngày kể từ lúc đăng nhập.
- Xoá một người khỏi danh sách thì phiên của người đó hết ngay.
- Thử sai nhiều lần bị làm chậm, không bị khoá hẳn (để người ngoài không khoá được đồng nghiệp bằng cách đoán ID). Bộ đếm lưu trong cơ sở dữ liệu nên khởi động lại không xoá nó. ID gõ sai không được ghi lại.
- Nhật ký "Việc gần đây" (tab Tự động hoá) ghi đăng nhập, thoát, lưu danh sách, tạo, duyệt, bỏ duyệt và xoá hộp, kèm ID người làm. Không ghi mã, link bảng tính hay ID gõ sai.
- Công tắc "Tự duyệt hộp khi đã chọn đủ" (tab Tự động hoá) áp dụng cho mọi hộp: khi bật, hộp được chọn đủ món sẽ được duyệt luôn, kể cả hộp của người làm.

## Hộp nháp tự mất sau một thời gian

Ứng dụng xoá hộp nháp không ai đụng tới sau `DRAFT_TTL_HOURS` giờ (mặc định 12) để trả món về kho. Trang `/dung` hiện còn bao lâu cho mỗi hộp nháp, và bảng "Tiến độ các hộp" của admin báo hộp nháp sắp mất. Nếu người làm hay duyệt muộn hơn 12 giờ, tăng `DRAFT_TTL_HOURS` trong Render.

## Hộp đã duyệt xong sau 2 ngày

Hộp làm ngày 11 xong từ 0 giờ ngày 13 (giờ Việt Nam). Món của hộp về kho và hộp rời khỏi danh sách. Một dòng ghi chú được giữ 30 ngày để `/dung` nhắc hộp cần trả (hôm qua và hôm kia) và chủ đề làm gần đây. Ứng dụng chỉ nhắc, việc trả hộp trong SM do script của nhóm. Đổi số ngày bằng `BOX_DONE_DAYS` trong Render (mặc định 2, 0 là không bao giờ xong).

## Khi chưa bật đăng nhập

Khi chưa bật đăng nhập, khung "Người dùng và quyền" và khung "Bảng tính Google" chưa lưu được gì. Lý do: ai có link cũng gọi được việc này, nên một ID cài sẵn có thể thành admin khi bạn bật đăng nhập. Hãy bật đăng nhập trước, rồi mới nhập danh sách và dán link.

## Quay lại bản cũ

Nếu quay lại bản cũ trên Render, bản cũ không có đăng nhập: trang admin và danh sách món lại công khai cho tới khi bạn lên lại bản mới. Biến `ADMIN_IDS`, `ADMIN_CODE` và các bảng mới trong cơ sở dữ liệu không làm hại bản cũ. Một chỗ cần cẩn thận: bản mới cho phép cùng tên hộp ở hai ngày khác nhau, còn bản cũ không khởi động được khi có hai hộp trùng tên. Trước khi quay lại, xoá hộp cũ hơn của mỗi cặp trùng tên (hoặc chờ hộp đó xong sau 2 ngày).

## Nếu bị khoá ngoài

Vào Render, xoá biến `ADMIN_IDS` (hoặc sửa lại cho đúng ID) rồi lưu. Ứng dụng chạy lại và trở về trạng thái mở. Nếu quên `ADMIN_CODE`, xoá biến đó, đăng nhập bằng ID, rồi đặt mã mới.

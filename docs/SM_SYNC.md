# Đồng bộ kho từ SM sang Box builder

Công cụ này là một script Tampermonkey chạy trong chính tab SM của bạn. Từ bản 0.2.0 nó làm ba việc: đọc kho đồ từ SM, đọc danh sách hộp
trong SM, và tạo hộp mới trong SM cho các hộp đã duyệt trong Box builder. Hai việc đầu **chỉ đọc**. Việc thứ ba chỉ chạy sau khi bạn
bấm nút **Xác nhận tạo**.
Script không bao giờ chuyển trạng thái, giao, mượn, sửa hay xoá hộp trong SM, và máy chủ Box builder không bao giờ kết nối tới SM.

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

## Đọc hộp trong SM

Bấm **Đọc hộp trong SM**. Bước này cũng chạy tự động ở cuối **Đồng bộ kho ngay**.

1. Script đọc `/api/boxes` của SM bằng lệnh GET, 500 hộp mỗi lần, đọc tiếp các trang sau, tối đa 2000 hộp mới nhất.
2. Với mỗi hộp, script chỉ giữ: mã hộp, tên, trạng thái, ngày tạo, và danh sách món kèm số lượng. Người được giao, người giữ,
   người tạo, lịch sử và mọi thông tin khác không được gửi đi.
3. Box builder dùng danh sách này để không chọn lại những món đang nằm trong hộp SM. Hộp đã trả không tính.

Kết quả hiện một dòng, ví dụ: `Box builder nhận 13 hộp: 11 đang dùng (giữ 49 món), 2 đã trả nên bỏ qua.`

## Đưa hộp sẵn sàng lên SM (hai bước)

Hộp sẵn sàng là hộp đã duyệt trong Box builder, qua đủ các kiểm tra, và chưa có trong SM. Khi bạn mở panel, dòng
**N hộp sẵn sàng đưa lên SM** cho biết có bao nhiêu hộp, kèm danh sách (tên hộp và số món). Bấm **Làm mới** để đọc lại.

1. **Xem và tạo trong SM.** Script đọc hộp trong SM (như phần trên), rồi đối chiếu tên. Hộp nào có tên trùng một hộp chưa trả
   trong SM được ghi là `đã có trong SM, bỏ qua`. Bước này **không tạo gì**. Cuối bước, script cho biết sẽ tạo bao nhiêu hộp
   và hiện nút **Xác nhận tạo N hộp**.
2. **Xác nhận tạo N hộp.** Chỉ nút này mới ghi vào SM. Script đọc lại danh sách một lần nữa, rồi tạo từng hộp bằng một lệnh
   `POST /api/boxes` từ chính tab SM của bạn, với tên hộp, mã món neo, danh sách món, ghi chú `Created by Box builder #<mã hộp>`
   và `borrow_now` luôn là `false` (không mượn, không giao ai). Tạo xong, script báo cho Box builder để hộp không bị đưa lên lần nữa.

Cách script tránh tạo trùng:

- Nút xác nhận tắt trong lúc chạy, và bấm lần hai không tạo thêm gì. Muốn tạo tiếp, bấm lại **Xem và tạo trong SM**.
- Một hộp lỗi không làm dừng các hộp khác. Mỗi hộp có một dòng kết quả. Riêng khi SM báo bạn chưa đăng nhập, script dừng ngay.
- Hộp bị bỏ qua vẫn nằm trong danh sách sẵn sàng của Box builder. Nếu hộp trùng tên đó không phải của Box builder, đổi tên hộp trong Box builder.

Lần đầu dùng với SM thật, chỉ để một hộp ở trạng thái sẵn sàng, bấm xác nhận rồi mở hộp đó trong SM để kiểm tra trước khi đưa nhiều hộp.

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
| Box builder nhận N hộp: ... | Đọc hộp trong SM xong | Không cần làm gì |
| Đã đọc N hộp mới nhất từ SM. Hộp cũ hơn không đọc | SM có hơn 2000 hộp, script chỉ đọc 2000 hộp mới nhất | Hộp cũ hơn có thể giữ món mà Box builder chưa biết. Kiểm tra tay nếu cần |
| SM không trả danh sách món trong hộp | SM trả hộp nhưng không kèm món | Box builder không biết hộp nào giữ món nào. Báo người phụ trách script |
| Kho đã đồng bộ, nhưng chưa đọc được hộp SM | Đồng bộ kho xong, bước đọc hộp lỗi | Làm theo lý do ghi cùng dòng, rồi bấm **Đọc hộp trong SM** |
| Danh sách hộp SM trống nên bị bỏ qua | SM trả về 0 hộp mà không báo tổng là 0, có thể đọc lỗi | Bấm lại. Nếu vẫn vậy, báo người phụ trách script |
| Chưa có hộp nào sẵn sàng đưa lên SM | Không có hộp đã duyệt chưa đưa lên SM | Duyệt hộp trong Box builder, rồi bấm **Làm mới** |
| Hàng đợi đưa hộp lên SM đang tắt | Công tắc hàng đợi trong Box builder đang tắt | Bật lại ở mục Tự động hoá |
| Hộp "...": đã có trong SM, bỏ qua | SM có hộp chưa trả cùng tên | Không cần làm gì. Nếu hộp đó không phải của Box builder, đổi tên hộp |
| Hộp "...": không còn sẵn sàng sau khi đọc SM | Món của hộp đã nằm trong một hộp SM khác, hoặc hộp vừa bị sửa | Xem mục Hàng chờ trong Box builder để biết lý do |
| Hộp "...": đã đổi từ lúc bạn xem, bỏ qua | Hộp bị sửa giữa hai lần bấm | Bấm lại **Xem và tạo trong SM** |
| Sẽ tạo N hộp ... Chưa tạo gì | Bước xem xong, chưa ghi gì vào SM | Bấm **Xác nhận tạo N hộp** nếu danh sách đúng |
| Hộp "...": SM không tạo được. SM từ chối: ... | SM không nhận hộp và nêu lý do | Sửa theo lý do (ví dụ món không còn trong kho SM), rồi bấm lại **Xem và tạo trong SM**. Các hộp khác vẫn được tạo |
| Hộp "...": đã tạo trong SM (mã) và báo cho Box builder | Tạo xong | Không cần làm gì |
| Hộp "...": đã tạo trong SM (mã) nhưng chưa báo được cho Box builder | Hộp đã có trong SM, nhưng Box builder chưa ghi nhận | Đừng tạo lại. Lần xem sau hộp sẽ hiện là đã có trong SM |
| Hộp "...": SM nhận yêu cầu nhưng không trả mã hộp | SM không nói mã hộp mới | Mở SM kiểm tra hộp đã có chưa trước khi tạo lại |
| Dừng lại, N hộp còn lại chưa tạo | SM báo chưa đăng nhập giữa chừng | Đăng nhập SM, bấm lại **Xem và tạo trong SM** |
| Xong: tạo X hộp, lỗi Y, bỏ qua Z | Tổng kết lần tạo | Đọc dòng của từng hộp lỗi |
| Box builder đang đòi mã truy cập chung | Box builder có đặt mã truy cập, script không gửi mã này | Báo người quản trị Box builder |
| Phiên bản Box builder này còn cũ, chưa có chức năng này | Máy chủ chưa có phần nhận hộp hoặc danh sách hộp sẵn sàng | Cập nhật Box builder |

## Điều cần biết

- SM không trả tên tiếng Việt cho món. Box builder lấy tên tiếng Việt từ file CSV của nó (theo Object ID). Món mới chưa có trong CSV và chưa có tên tiếng Việt sẽ bị bỏ qua cho đến khi file CSV được cập nhật.
- Chỉ món thuộc khu đã chọn, loại Obj, đang hoạt động và còn hàng mới được dùng.
- Box builder từ chối một lần đồng bộ nếu nó làm kho tụt dưới một nửa hoặc dưới 50 món, để một lần đọc lỗi không xoá mất kho.
- Link ảnh của SM là link ký sẵn nên hết hạn sau một thời gian. Đồng bộ lại thì ảnh hiện lại.
- Mã đồng bộ được lưu trong bộ nhớ riêng của Tampermonkey trên máy bạn, không nằm trong file script. Script chỉ gửi mã này cho Box builder, không bao giờ gửi cho SM.
- Mọi lệnh tới SM là GET, trừ một lệnh `POST /api/boxes` cho mỗi hộp bạn đã xác nhận tạo.
- Test trình duyệt: `python -I tests/userscript_sync_e2e.py` và `python -I tests/userscript_push_e2e.py` (cần playwright và chromium). Cả hai dùng SM giả, không chạm SM thật.

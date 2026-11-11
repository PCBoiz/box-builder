# Đồng bộ kho từ SM sang Box builder

Công cụ này là một script Tampermonkey chạy trong chính tab SM của **admin**. Người dùng thường không cài và không thấy nó. Từ bản 0.3.1 nó **chỉ đọc**:
đọc kho đồ từ SM (kèm số lượt mượn và ngày tạo của từng món) và danh sách hộp trong SM, rồi gửi cho Box builder để mọi người thấy món nào còn trống và hộp nào đã trả.
Script không tạo, giao, mượn, trả, sửa hay xoá gì trong SM, và máy chủ Box builder không bao giờ kết nối tới SM. Việc tạo hộp, giao, mượn và trả hộp trong SM do SCB làm,
không do script này (xem mục "Tạo hộp và trả hộp trong SM"). Script chạy ở mọi trang `sm.config.inc/boxes` và `sm.config.inc/object`.

**Script của nhóm (v2.23.0, "Sondeptraidatimthayban") là một công cụ khác.** Nó cũng đọc hộp đã duyệt từ Box builder và gửi kho lên Box builder,
nhưng ngoài việc tạo hộp nó còn gán người nhận và cập nhật hộp trong SM. Hai script không cần cùng chạy: nếu bật cả hai, nút của chúng chồng nhau
và cùng gửi kho. Nếu nhóm vẫn dùng script v2.23.0 để đồng bộ kho, số lượt mượn chỉ đến được Box builder khi bạn thêm hai trường vào hàm
`compactSmObject` của script đó: xem `docs/handoff/team_script_usage_patch.md`.

## Cài đặt (Firefox + Tampermonkey)

Có hai cách. Cách A nhanh nhưng phụ thuộc vào việc Tampermonkey tự mở trang cài đặt. Nếu bấm liên kết mà chỉ thấy **một trang trống**
(Tampermonkey đã chặn trang nhưng không mở được trang cài đặt) thì dùng cách B, cách này luôn dùng được.

**Cách A.** Trong Box builder, tab **Tự động hoá**, bấm **Cài bằng một cú bấm**, rồi bấm **Install** ở trang Tampermonkey mở ra.
Cách khác: Tampermonkey > **Dashboard** > **Utilities** > **Install from URL**, dán `https://box-builder-guwc.onrender.com/box_builder_sm.user.js`.

**Cách B (sao chép và dán).**

1. Trong Box builder, tab **Tự động hoá**, bấm **Sao chép mã công cụ**. Nếu trình duyệt không cho sao chép, mở mục
   "Không sao chép được?", bấm vào khung mã, nhấn Ctrl+A rồi Ctrl+C. (Hoặc mở `/box_builder_sm.txt` của trang, chọn hết và sao chép.)
2. Bấm biểu tượng Tampermonkey, chọn **Create a new script**.
3. Bấm vào khung soạn thảo, nhấn Ctrl+A để xoá nội dung mẫu, nhấn Ctrl+V để dán, nhấn Ctrl+S để lưu.
   Cách này không cần Box builder đã chạy: có thể dán thẳng file `integrations/box_builder_sm.user.js` trong thư mục dự án, rồi nhập địa chỉ ở bước 3 của phần dưới.

**Sau khi cài.**

1. Nếu đang có script đồng bộ cũ (tải từ GitHub bằng loader), tắt nó đi để hai nút không chồng nhau.
2. Mở https://sm.config.inc/boxes và đăng nhập SM như thường lệ. Góc dưới bên phải có nút **Box builder**.
3. Bấm nút đó, nhập:
   - **Địa chỉ Box builder**, ví dụ `https://box-builder-guwc.onrender.com`
   - **Mã đồng bộ**: giá trị `SYNC_TOKEN` (trong Render, mục Environment của dịch vụ box-builder)
   - **Khu**: `Hanoi-GPI`
4. Bấm **Kiểm tra kết nối**. Khi cả ba bước báo xong, bấm **Đồng bộ kho ngay**.

Lần đầu, Tampermonkey hỏi có cho script gọi tới địa chỉ Box builder không. Chọn cho phép (Always allow domain).

## Đọc hộp trong SM

Bấm **Đọc hộp trong SM**. Bước này cũng chạy tự động ở cuối **Đồng bộ kho ngay**.

1. Script đọc `/api/boxes` của SM bằng lệnh GET, 500 hộp mỗi lần, đọc tiếp các trang sau, tối đa 2000 hộp mới nhất.
2. Với mỗi hộp, script chỉ giữ: mã hộp, tên, trạng thái, ngày tạo, và danh sách món kèm số lượng. Người được giao, người giữ,
   người tạo, lịch sử và mọi thông tin khác không được gửi đi.
3. Box builder dùng danh sách này để không chọn lại những món đang nằm trong hộp SM. Hộp đã trả không tính.

Kết quả hiện một dòng, ví dụ: `Box builder nhận 13 hộp: 11 đang dùng (giữ 49 món), 2 đã trả nên bỏ qua.`

## Tạo hộp và trả hộp trong SM

Bản này không có nút tạo hộp. Trong file script vẫn còn đoạn mã tạo hộp (một lệnh `POST /api/boxes` sau khi bấm xác nhận) để người xem mã thấy nó làm gì, nhưng nó bị khoá:
hằng số `WRITES_ENABLED` là `false` và ba chỗ kiểm tra nó (bước xem không đưa ra danh sách tạo, hàm tạo hộp dừng ở dòng đầu, hàm gửi lệnh tạo dừng ở dòng đầu).
Khung công cụ ghi **Ghi vào SM: TẮT**. Test `tests/test_safety.py` và hai test trình duyệt chặn mọi thay đổi làm hằng số này bật mà không ai hay.

Hộp đã duyệt, kiểm tra của người hỗ trợ, tạo hộp trong SM, giao, mượn và trả hộp: xem `docs/KIEM_HOP.md` và phần SCB. Hộp thử đầu tiên trên SM thật cũng đi qua SCB.

## Làm mới tự động (từ bản 0.4.0)

Mặc định tắt. Khi admin bật công tắc trong khung công cụ và giữ tab SM mở, script tự đọc lại: danh sách hộp mỗi 10 phút (để hộp đã trả giải phóng món), kho đồ mỗi 60 phút.
Nó không bao giờ chạy chồng lên một lần đọc đang chạy, thử lại sau 5 phút nếu lỗi, và tự tắt khi SM báo chưa đăng nhập. Chỉ có lệnh đọc. Người dùng thường thấy
dòng "Kho SM: cập nhật ... trước" ở đầu trang mới hơn mà không phải bấm gì.

Khi mở panel, script hỏi `/api/preflight` một lần: máy chủ có thức không, bao nhiêu hộp sẵn sàng, dữ liệu cũ bao lâu, và bản script này có khớp bản mới nhất không.
Máy chủ miễn phí ngủ sau 15 phút không có ai dùng và dậy mất khoảng một phút: script báo "Máy chủ đang thức dậy" và thử lại, không báo lỗi mã đồng bộ.

## Cập nhật bản mới

Script không tự cập nhật (dòng `@downloadURL none`, không có `@updateURL`). Muốn dùng bản mới, mở `/box_builder_sm.txt` trên Box builder, chép toàn bộ,
dán đè vào script trong Tampermonkey rồi lưu. Dòng "Tự cập nhật: TẮT" trong khung công cụ xác nhận đúng như vậy.

## Đọc số lượt mượn (từ bản 0.3.0)

Khi đồng bộ kho, script đọc thêm hai thứ của mỗi món từ lệnh `GET /api/objects` đã dùng: số lượt mượn (SM gọi là LIFETIME USE, hiện ở cột cuối của trang `sm.config.inc/object`) và ngày tạo món. Không có lệnh ghi nào được thêm.

- Sau khi đồng bộ, dòng thông báo cho biết đọc được số lượt mượn của bao nhiêu món. Món SM không gửi số thì để trống, không bao giờ ghi là 0.
- Nếu thông báo nói SM không gửi số đọc được, chép hai dòng "Dạng SM gửi" gửi cho người phát triển. Dạng dữ liệu thật của SM chưa được kiểm chứng, nên lần đồng bộ đầu tiên cần bạn xem dòng này.
- Box builder dùng số này để vẽ biểu đồ ở "Cảnh báo kho" và để chọn ít hơn các món đã mượn từ 500 lượt trở lên (công tắc "Giảm món đã dùng nhiều" ở tab Tự động hoá).

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
| Phiên bản Box builder này còn cũ, chưa có chức năng này | Máy chủ chưa có phần nhận hộp hoặc danh sách hộp sẵn sàng | Cập nhật Box builder |

## Điều cần biết

- SM không trả tên tiếng Việt cho món. Box builder lấy tên tiếng Việt từ file CSV của nó (theo Object ID). Món mới chưa có trong CSV và chưa có tên tiếng Việt sẽ bị bỏ qua cho đến khi file CSV được cập nhật.
- Chỉ món thuộc khu đã chọn, loại Obj, đang hoạt động và còn hàng mới được dùng.
- Box builder từ chối một lần đồng bộ nếu nó làm kho tụt dưới một nửa hoặc dưới 50 món, để một lần đọc lỗi không xoá mất kho.
- Link ảnh của SM là link ký sẵn nên hết hạn sau một thời gian. Đồng bộ lại thì ảnh hiện lại.
- Mã đồng bộ được lưu trong bộ nhớ riêng của Tampermonkey trên máy bạn, không nằm trong file script. Script chỉ gửi mã này cho Box builder, không bao giờ gửi cho SM.
- Mọi lệnh tới SM là GET. Trong file còn một lệnh `POST /api/boxes` của đoạn mã tạo hộp, nhưng nó bị khoá bởi `WRITES_ENABLED = false`.
- Test trình duyệt: `python -I tests/userscript_sync_e2e.py` và `python -I tests/userscript_push_e2e.py` (cần playwright và chromium). Cả hai dùng SM giả, không chạm SM thật.

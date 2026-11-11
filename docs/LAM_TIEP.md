# Làm tiếp trên máy công ty: những gì đã làm ngày 11/11/2026 và việc còn lại

Tài liệu này viết cho chủ dự án (và cho phiên Claude chạy trên máy công ty). Đọc kèm `docs/HANDOFF.md` (chi tiết kỹ thuật, tiếng Anh). Kho mã công khai nên **không có mật khẩu, ID thật hay link bảng tính** trong tài liệu này hay trong mã.

Trang thật: https://box-builder-guwc.onrender.com (Render + Neon). Mã: `PCBoiz/box-builder`, nhánh `main`. Tại thời điểm viết, trang thật chạy commit `86b0838`; các cải tiến "trước buổi thử" (phần 2, mục 10) nằm ở commit mới nhất trên `main` sau đó.

## 1. Việc đầu tiên khi lên máy công ty

1. Lấy mã mới: `git pull` trong thư mục `box-builder`.
2. Đọc: phần này, rồi `docs/HANDOFF.md` mục 2 (luật cứng), `docs/LOGIN.md` (đăng nhập, biến Render), `docs/THU_NGHIEM.md` (buổi thử), `docs/SC_PROCESS.md` (quy trình làm hộp).
3. Chạy kiểm tra để chắc môi trường ổn (xem phần 6).
4. Làm các việc ở phần 4 theo thứ tự.

## 2. Những gì đã làm (theo nhóm)

1. **Đồ thị lượt mượn và giảm món.** SM đếm LIFETIME USE của từng món. Từ 500 lượt, món được chọn ít dần (cứ thêm 300 lượt thì cơ hội giảm một nửa); món mới mà mượn nhanh bị giảm nặng hơn món cũ (theo ngày tạo trong SM); có "mức loại hẳn" (0 là không loại món nào) và sàn số món còn để chọn. Công tắc "Giảm món đã dùng nhiều" ở tab Tự động hoá. Khung "Cảnh báo kho" có biểu đồ và danh sách món mượn nhiều nhất; tab Kho đồ có cột lượt mượn, sắp xếp và lọc. Công cụ `integrations/box_builder_sm.user.js` bản 0.3.0 gửi số lượt mượn (`uses`) và ngày tạo (`created`).
2. **Đăng nhập bằng ID công ty.** Danh sách ID do admin quản lý; admin theo biến `ADMIN_IDS`; mã admin `ADMIN_CODE` tuỳ chọn. Chưa đặt `ADMIN_IDS` thì ứng dụng mở như cũ (nên cập nhật mã không thể khoá admin ra ngoài). Phiên chỉ lưu bản băm, cookie `HttpOnly`, làm chậm khi gõ sai (đếm trong cơ sở dữ liệu). Các đường script của nhóm dùng (`/api/taskboxes`, `/api/sheet-boxes`, `/api/catalog/sync`, `/api/sm/boxes/sync`, `/api/sync/check`, `/api/boxes/<id>/pushed`) không bao giờ đòi đăng nhập; một bài test kiểm kê mọi đường để giữ điều đó. Đã **bỏ mã truy cập chung** `ACCESS_CODE`.
3. **Tài khoản người dùng có mật khẩu chung** (vòng 2 của chủ dự án). Biến Render `USER_IDS` (ID người dùng thường, vào được không cần dán danh sách) và `USER_PASSWORD` (một mật khẩu chung). Đặt hoặc đổi mật khẩu thì mọi phiên cũ hết hiệu lực. Mật khẩu và ID thật chỉ nằm trong Render.
4. **Trang người dùng `/dung`.** Người làm hộp tự tạo và **tự duyệt** hộp của mình (admin chỉ xem và can thiệp khi cần). Ba bước: Tạo hộp, Xem và duyệt, Lấy đồ (sao chép ID, in phiếu lấy đồ). Mỗi người chỉ thấy hộp của mình. Trạng thái nháp gọi là "Chờ duyệt". Hộp nháp không ai đụng tới sau 12 giờ thì tự mất, trang báo còn bao lâu.
5. **Vòng đời hộp.** Hộp đã duyệt xong sau **1 ngày** (giờ Việt Nam, từ 0 giờ ngày hôm sau): món về kho, hộp rời khỏi danh sách, một dòng ghi chú giữ 30 ngày (biến `BOX_DONE_DAYS`, mặc định 1; ban đầu chủ dự án chọn 2 rồi hạ xuống 1). Tên hộp (module + tên hộp) chỉ cần khác nhau **trong cùng một ngày** (cột `boxes.day`): một unit làm B2 mới mỗi ngày.
6. **Ngày làm việc của người dùng** (quy trình từ tài liệu của chủ dự án). Bảng "Hôm nay": dòng giờ hiện tại và mục tiếp theo; lịch cả ngày (2:30 đến 11:30: đầu ngày trả hộp, B1 3:30–5:30, B2 6:00–7:15 và 8:15–9:30, In giấy và Báo cáo cuối ngày cùng 9:00–10:00, B3 từ 9:30); hộp cần trả sáng nay (hôm qua và hôm kia; ứng dụng chỉ nhắc, việc trả hộp trong SM do script của nhóm); kế hoạch hôm nay (unit và ca: cả ngày, nửa ca đầu, nửa ca sau, chỉ B1 và B2, nghỉ) kèm "Cần làm" và nút Tạo B2/B3/A1; báo cáo cuối ngày để sao chép gửi quản lý (để trống cho tới khi lưu kế hoạch); khối "Gần đây ở ..." nhắc chủ đề đã làm gần đây trong cùng module. B1 không tạo trong ứng dụng ("đẩy B1 lên A1" là dùng lại hộp vật lý).
7. **Khung Bảng tính Google** ở tab Tự động hoá (link chỉ lưu khi đã bật đăng nhập), script bảng tính được phục vụ cùng địa chỉ của ứng dụng. Ứng dụng không ghi vào bảng tính.
8. **Câu chữ ngắn gọn cho người dùng.** Nghiên cứu repo `no-ai-slop` và tryemulate.ai (tryemulate.ai không hợp: nó viết lại cho "giống người" để qua máy dò AI, đòi 40 từ trở lên). Bộ hướng dẫn nằm ở `docs/handoff/van_phong_giao_dien.md`; đã áp dụng 40 chỗ sửa; bỏ hết chữ kỹ thuật trên `/dung` và đăng nhập (không mã lỗi, không "(ID …)", không "máy chủ"). Tên gọi thống nhất: Bỏ duyệt, Chưa chọn được đồ, lên SM, admin, AI, Chờ duyệt, món (đếm), kho. Một bài test cấm từ đệm trên các trang cho người dùng.
9. **Trang admin chỉ để xem.** Bảng "Tiến độ các hộp" (ai có mấy hộp, hộp sắp mất, hộp chưa chọn được đồ, hộp đã duyệt mà chưa lên SM sau 4 giờ), nhật ký "Việc gần đây", ô người dùng và quyền.
10. **Cải tiến trước buổi thử:** mục "Tôi là admin" có mũi tên và gạch chân; hộp dài trên điện thoại gọn hơn (khoảng một phần tư) kèm nút "Xuống Duyệt hộp"; sang ngày mới ô Module nhớ module gõ gần nhất; dòng "Kho còn N món trống." khi kho dưới 100 món.
11. **Kiểm thử và soát độc lập.** Bốn vòng review chỉ đọc (mỗi phát hiện có một người phản biện thử bác bỏ); mọi phát hiện được xác nhận đã sửa kèm test. Lỗi quan trọng đã bắt được: ID công ty lộ qua đường không cần đăng nhập (mà ID chính là thông tin đăng nhập); tên hộp bị giữ nhiều ngày làm một unit chỉ tạo được B2 cách ngày; ký tự `< > " '` trong tên hộp (script của nhóm ghi tên vào trang SM bằng `innerHTML`); chuyển hướng sau đăng nhập bằng `/.//evil`; phiên cũ còn sống sau khi đặt mật khẩu; báo cáo cuối ngày báo nhầm "mọi người làm cả ngày". Diễn tập 12 người dùng cùng làm 72 hộp trên máy chủ thử: không món nào nằm ở hai hộp, thăm dò dưới 0,1 giây.

Số liệu kiểm tra lúc đẩy: test máy chủ **880** pass trên SQLite (Python 3.14 và 3.12); e2e trình duyệt: trang người dùng **725**, admin **48**, tự động hoá (ui_automation) pass, biểu đồ pass, hai script (đồng bộ, đẩy hộp) pass; 3 bài node pass. **Chưa chạy được bộ test Postgres** (máy làm việc hôm 11/11 không có Postgres).

## 3. Quyết định của chủ dự án (đừng đổi khi chưa hỏi)

| Quyết định | Ghi chú |
|---|---|
| Người làm hộp tự tạo và tự duyệt; admin chỉ xem quy trình | Ban đầu tôi làm nhầm thành admin duyệt; đã sửa |
| Bỏ mã truy cập chung | Công cụ nội bộ, đừng làm bảo mật quá tay |
| Đăng nhập bằng ID công ty, danh sách do admin nhập | Có thêm mật khẩu chung cho người dùng thường (`USER_PASSWORD`) |
| Hộp xong sau 1 ngày | Món về kho; tên hộp dùng lại mỗi ngày |
| Nhãn nháp là "Chờ duyệt" | Trang admin vẫn gọi "Nháp" |
| "In giấy" và "Báo cáo cuối ngày" cùng 9:00 đến 10:00 | Sửa trong `app/static/dung.js` nếu đổi |
| B1 không tạo trong ứng dụng | |
| Nhắc trả hộp của hôm qua và hôm kia; ứng dụng chỉ nhắc | Không bao giờ chạm vào SM |
| Giảm món từ 500 lượt mượn, mới/cũ theo ngày tạo trong SM | Mức loại hẳn do chủ dự án chọn sau khi xem biểu đồ |
| Câu chữ ngắn, không "AI slop", thân thiện với người ít rành kỹ thuật | Hướng dẫn: `docs/WRITING.md` + `docs/handoff/van_phong_giao_dien.md` |
| Cứ thoải mái đẩy lên `main`, nhưng kiểm tra kỹ trước | Chưa bao giờ ghi vào SM, bảng tính hay Render từ phía Claude |

## 4. Việc còn lại, theo thứ tự

Những việc này cần tay bạn (Claude không được ghi vào Render, SM hay bảng tính):

1. **Render, mục Environment.** Đặt `ADMIN_IDS` (ID admin), `USER_IDS` (ID người dùng tạm; các ID cấp sau thêm vào cùng biến hoặc dán vào khung "Người dùng và quyền"), `USER_PASSWORD` (mật khẩu chung bạn đã chọn). Tuỳ chọn: `ADMIN_CODE`, `MAX_PARALLEL_LLM=2` hoặc 3 nếu nhiều người tạo hộp cùng lúc và phải chờ lâu. Nếu có `BOX_DONE_DAYS=2` thì đổi sang 1. Lưu, chờ khoảng một phút. Kiểm tra: `/api/auth/status` phải có `"enforced":true`.
2. **Đăng nhập admin**, vào tab Tự động hoá: dán danh sách người dùng ("ID, tên, module", thêm chữ `admin` ở cuối dòng nếu là admin) và link bảng tính.
3. **Công cụ trong SM:** cập nhật `integrations/box_builder_sm.user.js` lên 0.3.0 (hoặc vá script của nhóm theo `docs/handoff/team_script_usage_patch.md`), chạy "Đồng bộ kho" một lần. Dạng dữ liệu `_usage_count` và tên trường ngày tạo của SM **chưa được kiểm chứng**: nếu công cụ báo không đọc được số, nó in "Dạng SM gửi"; gửi hai dòng đó cho người phát triển.
4. **Chọn `hard_limit`** (loại hẳn từ N lượt mượn; 0 là không loại) sau khi xem biểu đồ và số món theo từng dải; kiểm tra các mặc định (500, giảm một nửa mỗi 300, ảnh hưởng của tuổi 50%, sàn 100 món).
5. **Thử MỘT hộp trên SM thật:** làm theo `docs/SM_SYNC.md`, mục "Thử MỘT hộp trên SM thật".
6. **Thử điền bảng tính bản sao** một lần (`docs/HANDOFF.md` mục 7, việc 5).
7. **Chạy bộ test Postgres** trên máy có Postgres (hướng dẫn trong `docs/HANDOFF.md` mục 4 và 5). Bảng mới (`people`, `sessions`, `login_fail`, `audit_log`, `box_history`, `plans`), cột `boxes.day` và chỉ mục `ux_box_name_day` mới chỉ được kiểm trên SQLite và bằng cách đọc SQL; trang thật đã khởi động và trả lời được trên Neon.
8. **Nhờ người giữ script của nhóm (v2.23.0)** thoát ký tự HTML khi ghi tên hộp vào trang SM (nó dùng `innerHTML`). Ứng dụng đã từ chối `< > " ' ` từ cửa vào, nhưng gốc rễ nằm ở script.
9. **Buổi thử chính thức:** theo `docs/THU_NGHIEM.md`.
10. Sau lần mở trang đầu tiên của bản mới, các hộp đã duyệt từ hơn 1 ngày trước sẽ bị dọn (món về kho): đúng quy tắc, nhưng sẽ xảy ra ngay lúc đó.

Gợi ý cải tiến tiếp (chưa làm):
- Nhãn "Đang chờ tới lượt" cho hộp đang xếp hàng chọn đồ (hiện chỉ ghi "Đang chọn đồ").
- Module nhớ theo người thay vì theo máy (trên máy dùng chung, người không có module riêng thấy module của người trước).
- Khôi phục sao lưu và nhập CSV chưa mang được hai hộp cùng tên ở hai ngày.
- Bấm đúp "Có, xoá hộp" hiện thông báo đỏ thừa.
- Trang in phiếu cho hộp nháp chưa có dòng "NHÁP".
- Đo tốc độ AI thật và Neon khi nhiều người cùng làm (diễn tập hôm 11/11 dùng bộ chọn giả).

## 5. Những chỗ chưa kiểm chứng hoặc có rủi ro

- Postgres chưa chạy test (xem trên). Render chỉ chuyển sang bản mới khi bản mới khởi động và qua kiểm tra sức khoẻ, nên nếu nâng cấp cơ sở dữ liệu lỗi thì bản cũ vẫn chạy.
- Bản cũ không khởi động được khi có hai hộp trùng tên ở hai ngày khác nhau (quay lại bản cũ cần xoá hộp cũ hơn của cặp trùng; xem `docs/LOGIN.md`).
- Khi chưa bật đăng nhập, `/api/state`, `/api/backup.json` và tổng quan của admin vẫn lộ tên người (trạng thái mở như trước đây).
- Mật khẩu chung nghĩa là ai biết mật khẩu và một ID thì vào được. Văn phòng dùng chung một địa chỉ mạng nên chia chung hạn mức gõ sai (30 lần trong 10 phút).
- Mọi người trên trang thật dùng chung một cơ sở dữ liệu Neon; gói Render miễn phí ngủ sau khoảng 15 phút không ai dùng (lần mở đầu chậm tới một phút).
- Không bao giờ gọi `/api/state`, `/api/taskboxes`, `/api/sheet-boxes`, `/api/my/*` trên trang thật chỉ để "xem thử": các đường này chạy việc dọn hộp đã xong.

## 6. Cách làm việc trên máy công ty

**Lấy mã và chạy kiểm tra** (Windows, PowerShell; Linux và macOS xem `docs/HANDOFF.md` mục 4 và 5):

```
git pull
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

Bài trình duyệt cần `pip install playwright`, `python -m playwright install chromium` và `npm install axe-core` (đặt `AXE_JS` trỏ tới `axe.min.js`). Mỗi bài tự mở một máy chủ riêng ở `127.0.0.1` (cổng tự chọn, cơ sở dữ liệu tạm), không bao giờ gọi SM, Google, Render hay Neon:

```
$env:AXE_JS = "<đường dẫn>\axe.min.js"
.venv\Scripts\python.exe -I -X utf8 tests\dung_e2e.py
.venv\Scripts\python.exe -I -X utf8 tests\admin_e2e.py
```

(và `ui_automation_e2e.py`, `usage_chart_e2e.py`, `userscript_sync_e2e.py`, `userscript_push_e2e.py`; `node tests\*.js` cho ba bài node.) Chạy từng bài trình duyệt một, không chạy song song (trùng cổng sẽ báo lỗi giả).

**Chạy một máy chủ thử** (giá trị giả, cơ sở dữ liệu tạm, bộ chọn giả):

```
$env:DB_PATH="data/local-test.db"; $env:CATALOG_PATH="data/catalog.csv"; $env:LLM_MODE="mock"
$env:ADMIN_IDS="1001"; $env:USER_IDS="4001"; $env:USER_PASSWORD="test-pass"; $env:SYNC_TOKEN="local-test-token"
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8766
```

Mở `http://127.0.0.1:8766/login`. Đừng dùng mật khẩu thật ở đây.

**Cách đã làm việc hiệu quả hôm 11/11:** chia việc cho nhiều agent (mỗi agent một nhóm tệp, không giẫm lên nhau), `/loop` để tiếp tục, Kanzo để khỏi quên đầu việc (bảng Kanzo của máy cũ không có trên máy công ty; các thẻ tóm tắt ở phần 2 và 4 của tài liệu này thay cho nó), và **soát độc lập chỉ đọc**: ba đến bốn người soát theo từng góc nhìn (đăng nhập và phân quyền; tương thích với script của nhóm và an toàn với SM; vòng đời hộp và dữ liệu; người dùng), rồi mỗi phát hiện có một người phản biện cố bác bỏ; chỉ phát hiện nào còn đứng vững mới sửa, kèm test. Cách này bắt được nhiều lỗi mà bài test không bắt được.

**Luật cứng** (chi tiết ở `docs/HANDOFF.md` mục 2): máy chủ không bao giờ ghi, sửa hay xoá gì trong SM và không giữ thông tin đăng nhập SM (SM chỉ được gọi từ trình duyệt đã đăng nhập của chính bạn); Claude không ghi vào bảng tính Google, Render hay COS; kho mã công khai nên không đưa mật khẩu, token, ID công ty thật, link bảng tính hay địa chỉ nội bộ vào mã, test, tài liệu; câu chữ cho người dùng theo `docs/WRITING.md`; mỗi lần đẩy chạy đủ kiểm tra và quét bí mật trước.

## 7. Các tệp mới quan trọng

| Tệp | Việc |
|---|---|
| `app/auth.py` | đăng nhập, phiên, làm chậm khi sai, cổng phân quyền (`klass()`), danh sách người dùng, nhật ký |
| `app/user_routes.py` | `/dung` và `/api/my/*` (hộp, duyệt, kế hoạch, hộp gần đây, phiếu in) |
| `app/overview.py` | trang admin xem quy trình (`/api/admin/overview`) |
| `app/sheet_panel.py` | khung Bảng tính Google |
| `app/usage.py` | công thức giảm món theo lượt mượn |
| `app/service.py` | thêm: vòng đời hộp (`retire_done`, `box_history`), kế hoạch ngày, báo cáo, ngày Việt Nam (`vn_day`), kiểm tra ký tự |
| `app/static/dung.*`, `login.*`, `usage_chart.*` | trang người dùng, đăng nhập, biểu đồ |
| `tests/test_auth*.py`, `test_day_flow.py`, `test_overview.py`, `test_usage_*.py`, `test_sheet_panel.py`, `test_review_fixes.py` | test máy chủ mới |
| `tests/dung_e2e.py`, `admin_e2e.py`, `usage_chart_e2e.py` | bài trình duyệt mới |
| `docs/LOGIN.md`, `THU_NGHIEM.md`, `LAM_TIEP.md` | tài liệu cho chủ dự án |
| `docs/handoff/van_phong_giao_dien.md`, `bao_cao_giam_mon_va_dang_nhap.md` | hướng dẫn câu chữ; báo cáo nghiên cứu về giảm món và đăng nhập (đồ thị, thang giảm, ID-only, nguồn tham khảo) |

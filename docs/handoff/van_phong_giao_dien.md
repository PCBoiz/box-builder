# Style guide for the interface text

For: Vietnamese people with little technical skill who make boxes of objects every day (Scenario Creators), and the admin who watches.
Goal: every sentence is friendly and clear, short, and has no padding. Friendly here means the reader never has to guess what to do. It does not mean extra words.

This guide sits on top of `docs/WRITING.md` (nine rules). It does not replace them. Part 1 lists what to add. Everything marked "my inference" is
my own judgment from knowledge of Vietnamese; I found no study that measures it (see `notes.md`).

"Words" below means the units separated by spaces (Vietnamese syllables), because that is what a reader sees.
The length targets are working targets, not measured limits.

## 1. Four rules to add to the nine

10. **Repeat the button's own words.** If a message says "Bấm Bỏ duyệt", the button says "Bỏ duyệt". Not "Mở lại", not "Bỏ duyệt hộp".
11. **Numbers come from the real setting.** Never write a time the code does not enforce ("vài giây", "15 giây", "dưới một phút" were three promises for one step).
    Round down when it is a promise ("còn khoảng 1 giờ", never more than there is).
12. **Say it once.** A guarantee repeated in five places gets changed in four. Put it where the decision is made.
13. **Say "bạn" when you mean the reader.** Not "người dùng", not "hệ thống sẽ". On the admin page, third person ("người làm hộp") is fine when it is about someone else.

## 2. Length

| Thing | Target | Hard limit |
|-------|--------|------------|
| Button | 1 to 3 words, starts with the verb | 4 |
| Heading | 2 to 5 words | 6 |
| One sentence | up to 15 words | 20 |
| Hint under a field or panel | 1 sentence | 2 sentences |
| Error | 2 short sentences, 3 if the reader can act on the cause | 30 words in all |
| Confirm question | one question plus one sentence on what changes | 25 words in all |
| Empty state | 2 short sentences | 25 words |
| Progress | "Đang" plus verb plus object, then the time if you know it | 12 words |
| Success | "Đã" plus verb plus object, then the next step only if it is not obvious | 15 words |

One idea per sentence. More than one "nếu" in a message means split it (see 5).

## 3. Each kind of text

**Button.** Verb first, names the result: Duyệt hộp, Bỏ duyệt, Chọn lại cả hộp, Điền ô trống, Đổi món, Xoá hộp. No "Nhấn vào đây để ...". No explanation next to a button whose label already says it.
If the label is not enough, add a hint that says *when* or *what changes*, never a repeat of the label.

**Heading.** Names what is under it, or what the person can do there. Plain nouns: "Tiến độ các hộp", "Người dùng và quyền". Not "Quy trình đang chạy", not a sentence.

**One-line hint.** Says what to type or what happens next, in the reader's words. Never describes the table or the screen ("Mỗi dòng cho biết ...", "Ở đây bạn chỉ xem ...").
If the field label already says it, write no hint.

**Error.** Three parts, in this order, as short as the facts allow:
1. What happened, in the app's words for the state ("Chưa chọn được đủ món").
2. Why, only if the person can use it ("Kho chỉ còn 8 món trống hợp nhóm đồ đã chọn, hộp cần 12").
3. What to do, naming the exact button ("Chọn thêm nhóm đồ rồi bấm Chọn lại").
No "Rất tiếc", no "Đã xảy ra lỗi", no "Vui lòng thử lại sau" with nothing else, no blame ("bạn đã nhập sai"). Say what to fix.
If the app cannot know the reason, say what the person can try, then who to tell: "Nếu vẫn thế, báo admin (nói tên hộp)."

**Confirmation.** Question with the object's name, then the one fact that matters. Button labels are verbs, not Có/Không:
"Xoá hộp M8 Box A1? Các món trong hộp sẽ trả về kho." with "Xoá hộp" and "Giữ lại".
Say what can be undone: "Bạn vẫn bỏ duyệt được cho đến khi hộp lên SM."

**Empty state.** What is missing and how to get one: "Chưa có hộp nào. Tạo hộp ở bước 1, hộp sẽ hiện ở đây." No "Hiện tại", no "Hãy bắt đầu hành trình".

**Progress.** "Đang chọn đồ. Thường mất dưới một phút." Say the time only if it is true for most runs. When it ends, say the result with a number: "Hộp M8 Box A1 đã chọn xong 12 món."

**Success.** "Đã duyệt hộp M8 Box A1." Add the next step only when it is not obvious: "Bạn lấy đồ ở bước 3." No "thành công", no "!".

## 4. What to cut

- Openers that announce: "Lưu ý rằng", "Cần lưu ý là", "Điều quan trọng là", "Dưới đây là", "Hãy cùng".
- Closers that repeat. Thanks, apologies, praise ("Cảm ơn bạn đã kiên nhẫn", "Xin lỗi vì sự bất tiện", "Tuyệt vời").
- Sentences that explain a button, a table or the screen.
- Cause the person cannot act on ("do mô hình trả về thiếu món hợp lệ", "máy chủ vừa khởi động lại?").
- Padding words: "một cách", "thật sự", "hoàn toàn", "vô cùng", "khá", "nhìn chung", "về cơ bản".
- Light verbs and nominalisations: "tiến hành xoá" is "xoá"; "thực hiện việc tạo hộp" is "tạo hộp"; "Việc tạo ... được thực hiện" is "Bạn tạo ...".
- Marketing adjectives (the nine rules already list them) and stacked synonym pairs ("nhanh chóng và hiệu quả", "an toàn và bảo mật").
- "Không chỉ ... mà còn ..." and "không phải X mà là Y" when you can say Y.
- Hedges stacked on each other ("có thể sẽ có khả năng"). Keep one, and only if the fact is really uncertain.
- The same idea in two sentences in a row ("Hộp đã duyệt nên chưa sửa được. Bấm Bỏ duyệt nếu cần sửa lại.": "Muốn sửa, bấm Bỏ duyệt.").
- "Hãy" more than once per message. One "Hãy" can soften a real instruction. A bare verb ("Bấm Chọn lại") is shorter and just as polite.

## 5. Conditions

Put the common case first and give each case its own short sentence. Use two "nếu" at most, and never inside each other.
Before: "Nếu hộp của bạn đã được duyệt thì bạn sẽ không thể sửa, trừ khi bạn bỏ duyệt, nhưng nếu hộp đã lên SM thì bạn cần nhờ admin."
After: "Hộp đã duyệt: bấm Bỏ duyệt để sửa. Hộp đã lên SM: nhờ admin."

## 6. Names: one word for one thing

| Thing | Use | Do not use |
|-------|-----|------------|
| A box | hộp. A kind of box keeps the name as typed: Box A1, B2 | khay, thùng |
| One object | món (counting and pointing: "12 món", "món này") | "đồ" for one thing |
| Objects as a mass | đồ only in: chọn đồ, lấy đồ, kho đồ | |
| A place in a box | ô ("ô trống") | chỗ, vị trí |
| The catalog | kho | danh mục |
| Approve / undo | Duyệt / Bỏ duyệt | Mở lại, phê duyệt, xác nhận |
| Draft | Nháp (see the decision below) | bản nháp, "chờ duyệt" as a second name |
| Not picked yet (error) | Chưa chọn được đồ | lỗi, bị lỗi, Cần xử lý |
| Box is in Scenario Manager | lên SM, đã lên SM | đẩy, đã đẩy |
| The thing that picks | say nothing ("Đang chọn đồ"), or "app". "AI" only for the batch feature | mô hình, hệ thống, máy, LLM |
| The role | admin | quản trị viên, người quản lý, chủ ứng dụng |
| The reader | bạn | người dùng, quý khách |
| A level | mức | ngưỡng |
| How long ago a box was made | tạo cách đây N ngày | tuổi |
| Keep an object when picking again | khoá món | (do not use "khoá" for security: write "ai biết ID là đăng nhập được") |
| A file | tệp (already the majority in the code). The team may say "file": pick one for the whole app | mixing both |

Decision for the owner: the user page shows the draft state as "Chờ bạn duyệt" (`user_routes.py:14`) and also says "hộp nháp" in text. That is two names for one state.
I suggest "Nháp" for the state and a separate line for the action ("Bấm Duyệt hộp khi ưng cả hộp"). The other choice, "Chờ bạn duyệt" everywhere on the user page, is also fine. Mixing is the only wrong answer.

## 7. Technical facts in plain words

| Fact | Say |
|------|-----|
| The server cannot be reached | Không kết nối được. Kiểm tra mạng rồi thử lại. |
| The session ended | Bạn đã bị đăng xuất. Đăng nhập lại. |
| Too many failed logins (rate limit) | Đã thử sai nhiều lần. Chờ 9 phút rồi thử lại. (minutes when 2 minutes or more) |
| A cross-site request was blocked | Thao tác này không đến từ trang Box builder nên bị chặn. Mở lại trang rồi thử lại. |
| The picker returned too few valid objects | AI chọn thiếu món dùng được. Bấm Điền ô trống để chọn tiếp. |
| Filters leave too few free objects | Chỉ còn 8 món trống hợp nhóm đồ đã chọn, hộp cần 12. Chọn thêm nhóm đồ. |
| Object already in another box (conflict) | Món 000123 (Cái bàn) đã nằm trong hộp M8 Box A1. |
| Draft expires (TTL) | Hộp nháp này tự mất sau khoảng 9 giờ nếu bạn chưa duyệt. |
| Box already pushed | Hộp đã lên SM nên không sửa hay xoá được ở đây. Cần đổi thì báo admin. |
| An environment variable is needed | Only in text for the owner: say where and what ("đặt ADMIN_IDS trong Render"). Not on the user page. |
| Do not use | máy chủ, phiên, token, API, JSON, request, filter, pool, queue, timeout |

## 8. Before and after (15)

"Before" is real (file and line) or marked as typical padded text I wrote to show the pattern.

| # | Verdict | Before | After | Note |
|---|---------|--------|-------|------|
| 1 | Fine | Hộp nháp này sẽ tự mất sau khoảng 9 giờ nếu chưa được duyệt. | (optional) Hộp nháp này tự mất sau khoảng 9 giờ nếu bạn chưa duyệt. | Specific number, says what happens. The passive "được duyệt" is acceptable; "bạn chưa duyệt" is a little more direct. |
| 2 | Fine | Đang chọn đồ. Thường mất dưới một phút. | same | What is happening and how long. |
| 3 | Fine | Chưa có hộp nào. Tạo hộp ở bước 1, hộp sẽ hiện ở đây. (`dung.js:44`) | same | Says what is missing and how to get one. |
| 4 | Fine | Bạn bấm Chọn lại cả hộp để thử lại. Nếu vẫn lỗi, xoá hộp rồi tạo lại hoặc báo admin. (`dung.js:49`) | same | Uses the exact button name. Only "lỗi" breaks the naming table. |
| 5 | Padded (typical) | Hộp của bạn đã được tạo thành công! Hệ thống đang tiến hành lựa chọn các món phù hợp, vui lòng chờ trong giây lát. | Đã tạo hộp M8 Box A1. Đang chọn đồ. | Cuts "thành công", "!", "hệ thống", "tiến hành", "vui lòng", "trong giây lát". |
| 6 | Padded (typical) | Rất tiếc, đã xảy ra lỗi trong quá trình chọn đồ. Vui lòng thử lại sau hoặc liên hệ quản trị viên để được hỗ trợ. | Chưa chọn được đủ món (còn 4 ô trống). Bấm Điền ô trống để chọn tiếp. | What happened, the number, the button. |
| 7 | Padded (typical) | Nhấn vào đây để thực hiện việc xoá hộp này khỏi danh sách. | Xoá hộp | The label is the whole message. |
| 8 | Padded (typical) | Hiện tại bạn chưa có bất kỳ hộp nào trong danh sách. Hãy bắt đầu bằng cách tạo hộp đầu tiên của bạn nhé! | Chưa có hộp nào. Tạo hộp ở bước 1. | |
| 9 | Padded (typical) | Hệ thống đang xử lý yêu cầu của bạn, quá trình này có thể mất một chút thời gian. Vui lòng không đóng trang. | Đang điền các ô trống của hộp M8 Box A1. | Names the object; no promise of "a little time". |
| 10 | Padded (real) | Hộp này đã được đẩy lên SM (Box ID 123). Hãy sửa trong SM, hoặc nhờ quản trị viên gỡ dấu "đã đẩy" nếu thật sự cần làm lại. (`service.py:511`) | Hộp đã lên SM (Box ID 123) nên không sửa hay xoá được ở đây. Cần đổi thì báo admin. | "đẩy", "gỡ dấu", "quản trị viên", "thật sự". |
| 11 | Padded (real) | ID chỉ cho biết ai đã làm hộp nào, nó không phải khoá bảo mật chắc chắn. Đừng cho người ngoài biết ID của bạn. (`login.html:31`) | Ai biết ID của bạn cũng đăng nhập được. Đừng đưa ID cho người ngoài công ty. | Consequence and action, not design. |
| 12 | Padded (real) | Hộp 17 không tồn tại (có thể đã bị huỷ). (`user_routes.py:45`) | Không thấy hộp này. Có thể nó đã tự mất vì quá lâu chưa duyệt. | The person never sees "17". |
| 13 | Padded (real) | Mỗi dòng cho biết, nếu đặt số ngày này, bao nhiêu hộp SM thôi giữ món và kho còn bao nhiêu món trống. Bấm nút ở cuối dòng để đổi số ngày. (`index.html:277`) | Bấm nút ở cuối một dòng để đổi số ngày dùng lại món. | Describes the table; the columns already say it. |
| 14 | Wrong (real) | Thêm chữ admin ở cuối dòng nếu người đó được duyệt hộp. (`index.html:323`) | Thêm admin ở cuối dòng nếu người đó cần vào trang admin. | Out of date: people approve their own boxes. |
| 15 | Padded (typical) | Nếu hộp của bạn đã được duyệt thì bạn sẽ không thể sửa, trừ khi bạn bỏ duyệt, nhưng nếu hộp đã được đưa lên SM thì bạn cần nhờ admin. | Hộp đã duyệt: bấm Bỏ duyệt để sửa. Hộp đã lên SM: nhờ admin. | One case per sentence. |

## 9. Check before shipping

1. **Deletion test.** Cut the sentence. Does the person lose anything they could act on? If not, leave it out.
2. **Portability test.** Could the sentence sit unchanged in any other app? Then it is filler. A real sentence names a box, a button, a number.
3. **Button test.** Every button name in the text exists on screen, spelled the same.
4. **Number test.** Every number comes from a setting or a count the code has.
5. **Read-aloud test.** Say it to a colleague at the next desk. If you would not say it that way, shorten it.
6. `tests/test_copy.py` catches banned words and em dashes only. Suggested additions for the user-page files (not for admin text, where some of these words are fine): `vui lòng`, `rất tiếc`, `xin lỗi`, `thành công`, `tiến hành`, `một cách`, `quản trị viên`, `đẩy lên SM`, `Mở lại`, `mô hình`. Expect a few false positives and review each hit.

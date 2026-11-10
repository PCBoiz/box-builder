# Vá nhỏ cho script đội (v2.23.0): gửi thêm số lần dùng và ngày tạo của món

Tài liệu này chỉ mô tả một thay đổi nhỏ. Nó không sửa script của đội. Đội tự sửa trong bản của mình, tự thử trong bản đó,
và tự nâng số phiên bản lên 2.24.0 trong repo của đội. Tài liệu không nhắc tới địa chỉ repo, mã, link bảng tính hay ID nào của đội.

## Vì sao

Box builder cần biết mỗi món đã được mượn bao nhiêu lần và món được tạo ngày nào, để chọn ít đi những món đã dùng quá nhiều.
SM hiện số lần dùng ở trang món là "LIFETIME USE", ví dụ "1587 borrows at least, 1611 items total, Heavily used".

Script đội v2.23.0 đã đọc từng trang `GET /api/objects?page=N` và gửi bản gọn lên `POST /api/catalog/sync`.
Chỉ hàm `compactSmObject` cần thêm hai trường: `uses` (số lần dùng) và `created` (ngày tạo).

- Không có lệnh mới tới SM. Dữ liệu lấy từ chính các trang `/api/objects` mà script đã đọc.
- Không ghi gì vào SM.
- Không gửi tên người, người được giao hay người giữ. Chỉ một số nguyên và một chuỗi ngày.
- Box builder bản cũ bỏ qua hai trường này, không báo lỗi. Bản mới nhận và lưu chúng.

## Dòng hiện tại trong v2.23.0

```js
  function compactSmObject(o) {
    const inv = o._inventory || {};
    return {
      id: o.id, name: o.name || '', vi: o.name_vi || '', type: o.type, status: o.status,
      avail: (typeof inv.available === 'number') ? inv.available : null,
      qty: o.quantity, loc: o.location || '', sector: o.sector || ''
    };
  }
```

## Sau khi sửa

Dán khối dưới đây ngay phía trên `function compactSmObject`, và thay hàm đó bằng bản mới ở cuối khối.

```js
  // Lifetime use (SM shows "LIFETIME USE: 1587 borrows at least, 1611 items total"). SM's JSON field is _usage_count, but its shape is
  // not verified: it may be a number, a numeric string, or an object. Only a whole number leaves this code. Anything else is null, never 0.
  const USE_KEYS = ['borrows', 'borrow_count', 'count', 'total', 'uses', 'used', 'value'];
  function wholeNumber(v) {
    let n = null;
    if (typeof v === 'number') n = v;
    else if (typeof v === 'string' && /^\s*\d+(\.\d+)?\s*$/.test(v)) n = Number(v);
    return n !== null && Number.isFinite(n) && n >= 0 && n <= Number.MAX_SAFE_INTEGER ? Math.round(n) : null;
  }
  function usesOf(o) {
    const u = o._usage_count;
    const direct = wholeNumber(u);
    if (direct !== null) return direct;
    if (u && typeof u === 'object' && !Array.isArray(u)) {
      for (const k of USE_KEYS) { const n = wholeNumber(u[k]); if (n !== null) return n; }       // the named keys first, in this order
      for (const k of Object.keys(u)) { const n = wholeNumber(u[k]); if (n !== null) return n; } // then the first number there is
    }
    return null;
  }

  // Creation date: a text with a digit in it goes as SM wrote it. A number of seconds or milliseconds since 1970 (a number, or a text of
  // only digits) goes as an ISO text. Anything else is null.
  const CREATED_KEYS = ['created_at', 'createdAt', 'created', 'date_created', 'first_seen'];
  function epochText(v) {
    const ms = v >= 1e9 && v < 4.2e9 ? v * 1000 : (v >= 1e12 && v < 4.2e12 ? v : null);
    return ms === null ? null : new Date(ms).toISOString();
  }
  function createdOf(o) {
    for (const k of CREATED_KEYS) {
      const v = o[k];
      if (typeof v === 'string') {
        const t = v.trim();
        const when = /^\d{9,13}$/.test(t) ? epochText(Number(t)) : (t && t.length <= 40 && /\d/.test(t) ? t : null);
        if (when !== null) return when;
      } else if (typeof v === 'number' && Number.isFinite(v)) {
        const when = epochText(v);
        if (when !== null) return when;
      }
    }
    return null;
  }

  function compactSmObject(o) {
    const inv = o._inventory || {};
    return {
      id: o.id, name: o.name || '', vi: o.name_vi || '', type: o.type, status: o.status,
      avail: (typeof inv.available === 'number') ? inv.available : null,
      qty: o.quantity, loc: o.location || '', sector: o.sector || '',
      uses: usesOf(o), created: createdOf(o)
    };
  }
```

Trong chính hàm `compactSmObject`, chỉ có hai chỗ đổi: dòng `qty: ...` thêm một dấu phẩy ở cuối, và có thêm dòng
`uses: usesOf(o), created: createdOf(o)`. Phần còn lại là các hàm đọc dữ liệu, đứng riêng để dễ thử.

## Cách đọc

| SM gửi `_usage_count` | `uses` |
|---|---|
| `1587` | 1587 |
| `"42"` | 42 |
| `{ "borrows": 1587, "borrow_units": 1611, "borrows_undercounted": false }` | 1587 (khoá `borrows` được ưu tiên) |
| `{ "borrow_units": 1611, "count": "320" }` | 320 (khoá có tên đứng trước số đầu tiên) |
| `{ "label": "heavy", "n": 7 }` | 7 (không có khoá quen thì lấy số đầu tiên) |
| `{ "count": 0 }` | 0 (SM nói 0 thì là 0) |
| không có, `null`, `"nhiều"`, danh sách, đúng/sai, số âm | `null` |

Thứ tự khoá ưu tiên: `borrows`, `borrow_count`, `count`, `total`, `uses`, `used`, `value`.
Món không đọc được số thì `uses` là `null`. Không bao giờ ghi 0 thay cho "không biết".

| SM gửi ngày | `created` |
|---|---|
| `created_at`, `createdAt`, `created`, `date_created` hoặc `first_seen` là chuỗi có chữ số, ví dụ `"2024-03-05T10:22:11Z"` | đúng chuỗi đó |
| cùng các khoá trên, là số giây hoặc mili giây kể từ 1970 (số hoặc chuỗi toàn chữ số) | chuỗi ISO, ví dụ `"2023-11-14T22:13:20.000Z"` |
| không có, rỗng, không có chữ số, hoặc số nhỏ không phải ngày | `null` |

Code thử các khoá theo thứ tự trên và lấy khoá đầu tiên cho ra một ngày đọc được.

## Dòng báo trạng thái (nên thêm)

Nếu không có dòng này, đội không biết SM có gửi số hay không. Trong `syncCatalogNow`, ngay sau khi lọc `rows` theo khu và trước `bbPush`:

```js
      const withUses = rows.filter(r => r.uses !== null).length;
```

Rồi thêm vào cuối dòng báo "Xong lúc ..." (chuỗi `bbSetStatus` có `r.usable`):

```js
      bbSetStatus(`Xong lúc ${new Date().toLocaleTimeString('vi-VN')}: ${r.usable} món dùng được trên ${r.received} món SM trả về. Số lần dùng đọc được ở ${withUses}/${rows.length} món.${warn}`, !!warn);
```

Nếu `withUses` là 0, SM gửi `_usage_count` ở dạng mà code chưa đọc được (xem phần dưới).

## Cách thử trong bản của đội

1. Lưu một bản sao file script hiện tại để quay lại được.
2. Sửa như trên, lưu trong Tampermonkey. Chưa nâng số phiên bản trong lúc thử.
3. Mở `https://sm.config.inc/object?sort_by=usage&sort_dir=desc&page=1` và ghi lại số lần dùng của món đầu tiên (ví dụ 1587).
4. Bấm đồng bộ kho như mọi ngày. Dòng trạng thái cuối phải ghi "Số lần dùng đọc được ở N/M món" với N lớn hơn 0.
5. Khi Box builder bản có số lần dùng đã chạy, mở tab Kho đồ và so số của món đầu tiên với trang SM. Hai số phải bằng nhau.
6. Nếu N là 0, mở F12, tab Network, chọn một lệnh `/api/objects?page=1`, xem trường `_usage_count` của một món.
   Chép tên các khoá và kiểu giá trị (số, chuỗi, nhóm) gửi cho người phát triển Box builder. Đừng chép tên người nếu có.

Việc này phải do người của đội thử trong bản của mình. Tài liệu này và các bài thử bên Box builder không chạm vào SM thật,
nên chưa ai xác nhận được code trên đọc đúng dạng thật của SM.

## Điều chưa kiểm chứng

- Dạng thật của `_usage_count` trong JSON của SM chưa được kiểm chứng. Code trên chịu được số, chuỗi số và nhóm có khoá giống hộp
  (`borrows`, `borrow_units`, `borrows_undercounted`). Nếu SM gửi dạng khác, `uses` là `null` và dòng trạng thái cho biết.
- Tên trường ngày tạo của món cũng chưa kiểm chứng. Code thử năm tên thường gặp. Nếu không tên nào cho ngày đọc được, `created` là `null`.
- Số "borrows at least" trên trang SM và số `uses` code đọc ra có thể khác nhau nếu SM đếm bằng hai trường. Lần thử đầu tiên sẽ cho thấy.

## Việc của đội

- Tự thử trong bản của mình (các bước trên).
- Tự nâng số phiên bản lên 2.24.0 trong repo của đội khi thấy kết quả đúng.
- Không cần đổi gì khác: nút, bảng và luồng tạo hộp giữ nguyên.

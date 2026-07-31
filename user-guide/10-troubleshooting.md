# Xử lý sự cố

> Dành cho: người vận hành và người phụ trách task  
> Kết quả: xác định đúng lớp bị lỗi trước khi thử chạy lại task.

## CLI agent hiển thị “not installed on host”

Agent Team kiểm tra lệnh khởi chạy đã cấu hình có tồn tại trên máy chủ Agent Manager
hay không.

Kiểm tra:

```bash
command -v npx
command -v cursor-agent
```

Sau đó kiểm tra các biến môi trường `AI_CODE_<ENGINE>_ACP_COMMAND` và
`AI_CODE_<ENGINE>_ACP_ARGS`. Với thực thi cách ly, kiểm tra thêm image của môi
trường có chứa engine và ACP adapter tương ứng.

## Claude hoặc Codex báo lỗi xác thực

Với local runtime:

- xác nhận người dùng hệ điều hành chạy Agent Manager đọc được thư mục đăng nhập;
- chạy thử CLI bằng đúng người dùng hệ điều hành đó;
- kiểm tra tiến trình trên máy chủ có cấu hình cần thiết.

Với OpenSandbox:

- xác nhận một môi trường AI Code Factory đang bật có
  `CLAUDE_CONFIG_DIR` hoặc `CODEX_HOME` tuyệt đối và hợp lệ;
- xác nhận thư mục tồn tại trên máy chủ OpenSandbox/Docker;
- đóng và tạo lại sandbox của task sau khi thay đổi thông tin xác thực được mount, vì mount
  được cố định tại thời điểm tạo sandbox.

## Không có `project-harness` trong không gian làm việc

Repository nguồn tồn tại dưới `community_plugins/` không tự động đưa pack vào
catalog Skill Packs.

Kiểm tra:

- plugin Skill Packs đã bật;
- pack xuất hiện trong **Skill Packs**;
- source sync không có lỗi;
- planning skill của board là `project-harness` hoặc một pack hợp lệ khác;
- folder của pack có `SKILL.md` hợp lệ.

Pack bị thiếu sẽ được ghi log và bỏ qua, vì vậy lượt chạy có thể tiếp tục bằng
hướng dẫn dự phòng thay vì báo lỗi rõ ràng.

## Repository bị thiếu trong task

Kiểm tra:

- repository đã được đăng ký ở cấp hệ thống;
- repository đã được gán cho board;
- thông tin xác thực có thể tải dữ liệu từ remote;
- bản sao Git chuẩn đang hoạt động bình thường;
- dùng thao tác chuẩn bị lại trong cockpit nếu thực sự cần bản sao mới.

## Kiểm thử đạt nhưng agent đánh giá vẫn kết luận không đạt

Kiểm tra biên nhận lệnh:

- Lệnh có nằm trong `TASKS.json` đã phê duyệt không?
- Lệnh có chạy trong đúng repository được chỉ định không?
- Tệp mã nguồn có thay đổi sau khi lệnh chạy không?
- Biên nhận có được `EVIDENCE.json` tham chiếu không?
- Mỗi tiêu chí chấp nhận đã được ánh xạ tới bằng chứng chưa?
- Hồ sơ UI/hình ảnh có kịch bản và tệp bằng chứng không rỗng không?

Log terminal do agent dán vào không thể thay thế biên nhận của backend.

## Task UI không có ảnh chụp màn hình hữu ích

Kiểm tra:

- deploy/test skill đã được chọn;
- MCP trình duyệt đã được cấu hình cho đúng agent CLI;
- bước chuẩn bị phiên kiểm thử đã xác thực chạy trước công cụ trình duyệt đầu tiên;
- trình duyệt dùng kích thước màn hình desktop thực tế;
- tệp bằng chứng được lưu trong không gian làm việc của task;
- ảnh chụp thể hiện hành vi cần chấp nhận, không chỉ là màn hình đang tải hoặc
  đăng nhập.

## Task đang chờ con người

Mở planning, loop và journal panel. Các nguyên nhân phổ biến:

- câu hỏi chặn luồng;
- agent đánh giá trả về `needs_human`;
- đã đạt giới hạn số lần thử, token, chi phí hoặc thời gian chạy;
- có yêu cầu thay đổi kế hoạch;
- môi trường thực thi hoặc thông tin xác thực có vấn đề mà agent không thể tự
  phỏng đoán an toàn.

Hãy trả lời hoặc phê duyệt đúng yêu cầu đang hiển thị. Không nên khởi động lại
một cách mù quáng: nhật ký và bản tóm tắt bằng chứng giải thích lần thử tiếp theo
cần gì.

## Sandbox không nhận cấu hình mới

Repository mount, thông tin xác thực, image và chính sách mạng được thiết lập khi
sandbox được tạo. Pause/resume tiếp tục sử dụng sandbox hiện tại.

Đóng sandbox của task từ cockpit hoặc trang Sandboxes, sau đó bắt đầu lượt mới để
tạo sandbox từ cấu hình đã cập nhật.

## Notification channel không gửi message

Kiểm tra theo thứ tự:

1. board channel đang bật;
2. event hiện tại đã được tick trong allowlist; backend hiện không coi allowlist
   trống là “gửi tất cả”;
3. connection còn bot token;
4. Channel ID thuộc đúng Mattermost/Slack workspace;
5. bot đã tham gia channel;
6. **Send test** có thành công không;
7. **Recent deliveries** ghi lỗi gì.

Mattermost `401` thường liên quan token/server URL. `403` thường do bot chưa ở
trong channel hoặc Channel ID thuộc một Mattermost instance khác. Xem
[Cấu hình notification channels](14-notification-channels.md).

Nếu dialog của board không có connection để chọn, hãy kiểm tra connection có
thuộc chính tài khoản đang cấu hình board không. Connection hiện được lọc theo
owner.

## Nên kiểm tra ở đâu trước?

| Triệu chứng | Nơi kiểm tra đầu tiên |
|---|---|
| Agent không khởi động | Lỗi lượt chạy và trạng thái sẵn sàng của CLI |
| Xác thực thất bại | Môi trường AI Code và thông tin xác thực được mount |
| Sai codebase | Repository được gán cho board |
| Agent không theo quy trình | Danh mục skill và các skill board đã chọn |
| Không dùng được công cụ bên ngoài | Cấu hình MCP theo agent và chính sách mạng của sandbox |
| Kiểm thử/bằng chứng bị từ chối | Hiệu lực của biên nhận và ánh xạ bằng chứng của agent đánh giá |
| Lặp lại cùng một nhầm lẫn | Nhật ký task và bảng Friction của board |
| Không nhận notification | Board channel và Recent deliveries |

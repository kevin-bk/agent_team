# Chạy task qua engineering loop

> Dành cho: BA, Product Owner, lập trình viên, QA hoặc trưởng nhóm  
> Kết quả: tạo task, có kế hoạch được phê duyệt và bắt đầu thực thi có xác minh.

Trang này hướng dẫn chế độ có kiểm soát chặt. Nếu muốn trao đổi trực tiếp với
Claude/Codex theo từng lượt — workflow phổ biến cho công việc hằng ngày — hãy đọc
[Chat trực tiếp với Claude hoặc Codex](15-direct-cli-chat.md).

## 1. Viết task đủ rõ ràng

Một task tốt cho agent lập kế hoạch biết kết quả cần đạt được nhưng không áp đặt mọi chi
tiết triển khai.

Nên bao gồm:

- vấn đề của người dùng hoặc nghiệp vụ;
- hành vi có thể quan sát được sau khi hoàn thành;
- ràng buộc và những nội dung không thuộc phạm vi;
- liên kết hoặc ảnh chụp màn hình giúp làm rõ hành vi hiện tại;
- tiêu chí chấp nhận đã biết.

Ví dụ:

> Thêm tùy chọn trong trang quản trị để merchant có thể bật hoặc tắt email bản
> ghi hội thoại. Thiết lập phải được lưu riêng theo shop, hành vi chat trên
> storefront không thay đổi, và thay đổi phải được xác minh trên UI quản trị
> nhúng.

Không nên viết:

> Fix email.

Agent lập kế hoạch có thể nghiên cứu phần còn mơ hồ, nhưng không nên bị buộc phải
tự tạo ra ý định sản phẩm.

## 2. Kiểm tra bối cảnh làm việc của task

Xác nhận board đã cung cấp:

- các repository liên quan;
- agent lập trình;
- agent đánh giá;
- skill lập kế hoạch và skill riêng của dự án;
- MCP phục vụ triển khai, cơ sở dữ liệu hoặc trình duyệt nếu cần.

Repository của task được chuẩn bị thành một bản sao làm việc riêng. Bản sao này
được giữ lại qua các lần chạy để agent tiếp tục branch và lịch sử công việc.

## 3. Bắt đầu lập kế hoạch

Khởi động luồng lập kế hoạch nghiêm ngặt và chọn:

- agent lập kế hoạch;
- agent đánh giá kế hoạch tùy chọn;
- agent lập trình/triển khai;
- agent đánh giá;
- giới hạn số lần thử và giới hạn tài nguyên.

Lập kế hoạch là một công việc hữu hạn. Agent ghi các tệp kế hoạch rồi dừng; không
có tiến trình hay lượt ACP nào tiếp tục chạy trong lúc chờ con người phê duyệt.

## 4. Đánh giá và phê duyệt

Đọc `SPEC.md`, `PLAN.md` và `TASKS.json`. Chỉ phê duyệt khi:

- phạm vi khớp với yêu cầu;
- những nội dung không thuộc phạm vi được nêu rõ;
- tiêu chí chấp nhận có thể quan sát hoặc kiểm thử;
- mỗi lệnh dự kiến chạy trong đúng repository;
- thay đổi UI yêu cầu bằng chứng UI/E2E;
- rủi ro và phương án quay lui hợp lý.

Nếu kế hoạch sai, chọn **Request changes**. Việc sửa tệp đã phê duyệt sẽ làm phê
duyệt mất hiệu lực vì hệ thống đã ghim checksum của kế hoạch đó.

## 5. Chạy và theo dõi

Chọn **Approve and run**. Cockpit sẽ hiển thị trực tiếp:

- nội dung trả lời và quá trình suy luận của agent;
- thẻ công cụ và tiến trình chạy lệnh;
- tệp và thay đổi mã nguồn;
- tệp kế hoạch;
- trạng thái vòng lặp;
- bằng chứng xác minh;
- mục nhật ký.

Loop kết thúc ở một trong các trạng thái có ý nghĩa sau:

| Trạng thái | Ý nghĩa |
|---|---|
| Complete | Agent đánh giá kết luận đạt, có bằng chứng và được backend chấp nhận |
| Waiting for human | Cần quyết định, đã chạm giới hạn hoặc quy tắc an toàn |
| Plan change requested | Quá trình thực thi phát hiện kế hoạch đã duyệt không an toàn hoặc không đúng |
| Waiting for answers | Agent đưa ra câu hỏi chặn luồng |
| Failed / Cancelled | Lượt chạy bị lỗi hoặc con người hủy |

Nếu board đã cấu hình channel, các trạng thái cần hành động và trạng thái kết
thúc sẽ được gửi tới Mattermost/Slack theo event allowlist. Việc đang chạy bình
thường không tạo notification liên tục.

## 6. Đánh giá kết quả

Không chỉ đọc câu trả lời cuối cùng. Hãy kiểm tra:

- thay đổi mã nguồn;
- biên nhận lệnh;
- ảnh chụp màn hình hoặc tệp kết quả kịch bản;
- kết luận của agent đánh giá;
- quyết định và vấn đề quy trình trong nhật ký;
- branch của task hoặc pull request đã được đẩy lên, nếu tính năng này được bật.

Tiếp theo: [Lập kế hoạch và phê duyệt](06-planning-and-approval.md). Nếu muốn
hiểu vì sao generator và evaluator lặp lại, đọc
[Engineering loop](13-engineering-loop.md).

# Lập kế hoạch và phê duyệt

> Dành cho: Product Owner, BA, lập trình viên, QA và trưởng nhóm  
> Kết quả: hiểu chính xác nội dung đang được phê duyệt và cách đánh giá an toàn.

## Kế hoạch là cam kết thực hiện, không phải một tin nhắn chat

Chế độ lập kế hoạch nghiêm ngặt tạo các tệp bền vững trong `.agent-team/`:

| Tệp | Ý nghĩa dễ hiểu |
|---|---|
| `SPEC.md` | Thành công nghĩa là gì: phạm vi, nội dung không làm, ràng buộc, tiêu chí chấp nhận và rủi ro |
| `PLAN.md` | Nhóm dự kiến triển khai, xác minh và quay lui như thế nào |
| `TASKS.json` | Sơ đồ công việc và quy ước xác minh mà máy có thể đọc |
| `PLAN_REVIEW.json` | Kết quả đánh giá độc lập đối với kế hoạch được đề xuất |
| `INTAKE.json` | Cờ rủi ro được backend dùng để phân loại luồng lập kế hoạch |
| `QUESTIONS.json` | Câu hỏi chặn hoặc không chặn dành cho con người |

`SPEC.md` và `PLAN.md` là tài liệu hướng dẫn. Backend kiểm tra chúng tồn tại
nhưng không phân tích tiêu đề. `TASKS.json`, `PLAN_REVIEW.json`, `INTAKE.json` và
`QUESTIONS.json` là các quy ước được backend đọc theo schema cố định.

## `project-harness` làm gì?

Skill lập kế hoạch hướng dẫn agent chọn mức độ nghiêm ngặt phù hợp:

- **quick:** thay đổi nhỏ, ít rủi ro;
- **normal:** rủi ro vừa phải hoặc ảnh hưởng đến nhiều khu vực;
- **risk:** ảnh hưởng tới bảo mật, phân quyền, mô hình dữ liệu, bí mật, kiểm toán
  hoặc hệ thống bên ngoài.

Backend tự tính lại phân loại từ các cờ rủi ro; không tin trực tiếp nhãn do agent
ghi. Nếu skill pack bị thiếu, prompt của agent vẫn có hướng dẫn lập kế hoạch dự
phòng và backend vẫn áp dụng quy tắc phân loại. Tuy nhiên, skill đầy đủ vẫn được
khuyến nghị vì cung cấp hướng dẫn chi tiết hơn.

## Danh sách kiểm tra dành cho người phê duyệt

### Ý định (Intent)

- Mục tiêu có giải quyết đúng vấn đề được yêu cầu không?
- Những nội dung không làm đã được liệt kê chưa?
- Agent lập kế hoạch có tự giả định một quyết định sản phẩm đáng lẽ con người phải quyết định
  không?

### Phạm vi (Scope)

- Đã chọn đúng repository chưa?
- API, mô hình cơ sở dữ liệu, khu vực UI và tích hợp bị ảnh hưởng đã được xác định chưa?
- Kế hoạch có đưa thêm việc tái cấu trúc không liên quan không?

### Tiêu chí chấp nhận (Acceptance)

- Mỗi tiêu chí có thể quan sát hoặc kiểm thử không?
- Mỗi luồng người dùng quan trọng đã có tiêu chí chưa?
- Với công việc UI, kế hoạch có yêu cầu kịch bản chạy trên trình duyệt thật không?

### Xác minh (Verification)

- Mỗi lệnh có chỉ rõ repository nơi nó phải chạy không?
- Kiểm thử tập trung và kiểm thử hồi quy có phù hợp không?
- Kịch bản UI/hình ảnh và tệp bằng chứng có được yêu cầu khi cần không?
- Thay đổi rủi ro có phương án quay lui không?

## Cơ chế phê duyệt

Approval sẽ:

- kiểm tra các tệp bắt buộc;
- ghim checksum của tệp và dấu vân tay chuẩn của kế hoạch task;
- ghi nhận người phê duyệt;
- chuyển task sang `plan_approved`;
- không nhất thiết bắt đầu thực thi, trừ khi dùng **Approve and run**.

Thay đổi lựa chọn repository, lệnh, tiêu chí chấp nhận, dependency hoặc phạm vi
khác sau khi phê duyệt sẽ làm kế hoạch mất hiệu lực. Kế hoạch phải được đánh giá
và phê duyệt lại.

## Câu hỏi và thay đổi kế hoạch

Câu hỏi chặn đưa task sang `waiting_answers`. Con người có thể viết câu trả lời
dạng văn bản tự do; backend sẽ gắn câu trả lời đó với các câu hỏi đang chờ. Câu
hỏi không chặn xuất hiện trong lúc đánh giá kế hoạch nhưng không làm quy trình
tạm dừng.

Nếu trong lúc triển khai, agent phát hiện kế hoạch đã duyệt không an toàn hoặc
sai về bản chất, agent sẽ tạo yêu cầu thay đổi kế hoạch. Đây không phải lỗi mà là
luồng kiểm soát dành cho thông tin mới. Con người giải quyết thay đổi, đánh giá
kế hoạch mới và phê duyệt lại.

Tiếp theo: [Kiểm thử và xác minh](07-testing-and-verification.md).

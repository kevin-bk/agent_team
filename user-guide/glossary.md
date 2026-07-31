# Bảng thuật ngữ

## ACP

Agent Client Protocol. Kết nối có cấu trúc mà Agent Team dùng để điều khiển
Claude Code, Codex hoặc Cursor và nhận văn bản trực tiếp, kế hoạch, hoạt động công
cụ, yêu cầu cấp quyền và mức sử dụng.

## Agent Manager

Ứng dụng chủ và trung tâm điều khiển quản lý plugin, người dùng, agent, thông tin
xác thực, môi trường AI Code và skill pack.

## Agent Team

Plugin điều phối cung cấp board, task, không gian làm việc, lập kế hoạch, vòng lặp
thực thi, bằng chứng, nhật ký và tích hợp.

## Engineering loop

Vòng điều phối bên ngoài coding agent: generator triển khai, backend chạy kiểm
tra, evaluator đánh giá và controller quyết định tiếp tục hay dừng. Xem
[Engineering loop](13-engineering-loop.md).

## Board

Cấu hình bền vững của một dự án/luồng công việc, bao gồm thành viên, agent CLI,
repository, skill, MCP server, quy tắc làm việc và task.

## Biên nhận lệnh (Command receipt)

Bản ghi đáng tin cậy do backend/môi trường thực thi tạo, chứng minh lệnh xác minh
đã được phê duyệt và chạy với một trạng thái mã nguồn, môi trường cụ thể.

## Direct CLI agent

Engine lập trình Claude, Codex hoặc Cursor có bí danh `cli:<engine>`, được điều
khiển trực tiếp qua ACP mà không cần bộ điều phối LLM riêng.

## Evaluator

Agent độc lập đánh giá bằng chứng triển khai. Khi cần xác minh chặt chẽ, agent
đánh giá không nên đồng thời giữ vai trò agent lập trình.

## Evidence

Bằng chứng có cấu trúc cho thấy tiêu chí chấp nhận đã được đáp ứng, bao gồm biên
nhận, kịch bản, ảnh chụp màn hình và tệp khác trong workspace.

## Friction

Mục nhật ký mô tả vấn đề trong quy trình hoặc môi trường khiến task khó hơn cần
thiết và có thể tiếp tục ảnh hưởng đến các task sau.

## Generator

Agent lập trình chịu trách nhiệm triển khai kế hoạch đã được phê duyệt.

## Journal

Lịch sử theo ngữ nghĩa chỉ được ghi thêm, lưu lại quyết định, giả định, câu hỏi,
câu trả lời, phê duyệt, thay đổi kế hoạch, kết luận và vấn đề quy trình. Nhật ký
không phải log lệnh thô.

## MCP

Model Context Protocol. Tiêu chuẩn cấp công cụ cho agent để tương tác với hệ
thống bên ngoài như nền tảng triển khai, cơ sở dữ liệu, trình duyệt hoặc công cụ
theo dõi vấn đề.

## Notification connection

Bot account và credential Mattermost/Slack có thể được nhiều board dùng chung.
Mỗi board liên kết connection này với một Channel ID và quy tắc event riêng.

## OpenSandbox

Provider thực thi cách ly tùy chọn. Agent Team thường tạo một sandbox cho mỗi
task và tạm dừng/tiếp tục sandbox qua nhiều lượt.

## `project-harness`

Skill pack hướng dẫn agent phân loại rủi ro và chọn độ sâu lập kế hoạch/xác minh.
Nó được lưu trong một Git repository nhưng phải được import qua Skill Packs để
Agent Team đưa vào task workspace.

## Skill lập kế hoạch (Planning skill)

Skill pack hướng dẫn cấu trúc và mức độ nghiêm ngặt của `SPEC.md`, `PLAN.md`.
Tên mặc định là `project-harness`.

## Skill pack

Bộ hướng dẫn dạng thư mục có thể tái sử dụng, bao gồm `SKILL.md` và các script,
tài liệu tham khảo hoặc tài nguyên tùy chọn.

## Không gian làm việc của task (Task workspace)

Thư mục dùng chung của task, chứa repository đã chuẩn bị, tệp đính kèm, tệp kế
hoạch, bằng chứng, nội dung đọc lại từ nhật ký và tệp được tạo ra.

## Verdict

Kết luận có cấu trúc của agent đánh giá: đạt, không đạt hoặc cần con người.
Backend vẫn áp dụng quy tắc bằng chứng trước khi chấp nhận hoàn thành task.

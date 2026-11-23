/**
 * Chính sách quyền riêng tư (bước 7, P1). Phải trùng PRIVACY_POLICY_VERSION ở backend/config.py:
 * đổi nội dung thì tăng cả hai, người dùng cũ sẽ được hỏi đồng ý lại.
 * ⚠ Nội dung cần luật sư / pháp chế duyệt trước khi dùng với khách thật (docs/BAO_MAT_VA_DU_LIEU_CA_NHAN.md).
 */
export const PRIVACY_VERSION = "2026-11-23";

export const PRIVACY_CONTACT =
  process.env.NEXT_PUBLIC_PRIVACY_CONTACT || "trưởng đội của bạn hoặc đơn vị vận hành FieldOps";

export type PolicySection = { title: string; items: string[] };

export const POLICY_SECTIONS: PolicySection[] = [
  {
    title: "1. Chúng tôi thu thập gì",
    items: [
      "Tài khoản: email, mật khẩu (chỉ lưu dạng đã băm), cách đăng nhập (mật khẩu hoặc Google).",
      "Công việc: ca được giao, nhật ký thao tác, ảnh hiện trường (đã xoá vị trí GPS trong ảnh), biên bản và chữ ký khách khi đóng ca.",
      "Thông tin khách hàng của đội: tên hoặc nhãn khách, địa chỉ, toạ độ, thiết bị. Phần này do đội nhập để làm việc.",
      'Vị trí GPS của điện thoại: chỉ khi bạn bấm "Tôi đang ở nhà khách", và chỉ lưu thành toạ độ của ca.',
      "Câu hỏi gửi trợ lý tài liệu; mã nhận thông báo của điện thoại nếu bạn bật thông báo.",
    ],
  },
  {
    title: "2. Dùng để làm gì",
    items: [
      "Giao việc, xếp lịch và tuyến đường, ghi nhận và đóng ca sửa chữa.",
      "Trả lời câu hỏi dựa trên tài liệu của đội; gửi thông báo khi có ca mới hoặc lịch đổi.",
      "Bảo mật tài khoản, chống lạm dụng, sao lưu và khôi phục dữ liệu.",
    ],
  },
  {
    title: "3. Ai nhận dữ liệu (có thể ở nước ngoài)",
    items: [
      "Máy chủ Oracle Cloud nơi đặt hệ thống.",
      "Google Gemini: câu hỏi và đoạn tài liệu liên quan, để trợ lý trả lời.",
      'OpenStreetMap (Nominatim, châu Âu): chỉ chuỗi địa chỉ khách, khi bấm "Tìm toạ độ".',
      "Expo và Google (Mỹ): mã nhận thông báo và số ca, giờ (không có thông tin khách), khi bạn bật thông báo.",
      "Google: email khi bạn đăng nhập bằng Google.",
    ],
  },
  {
    title: "4. Lưu bao lâu",
    items: [
      "Ảnh và chữ ký của ca đã xong: xoá sau 365 ngày.",
      "Tài khoản và dữ liệu công việc: đến khi bạn xoá tài khoản hoặc đội ngừng sử dụng.",
      "Bản sao lưu: giữ 7 bản gần nhất.",
    ],
  },
  {
    title: "5. Quyền của bạn",
    items: [
      'Xem và tải dữ liệu cá nhân của mình: Cài đặt → "Tải dữ liệu của tôi".',
      "Sửa thông tin tài khoản; yêu cầu trưởng đội sửa thông tin ca.",
      'Xoá tài khoản: Cài đặt → "Xoá tài khoản". Đội chỉ có mình bạn sẽ bị xoá cùng toàn bộ ca.',
      "Rút lại sự đồng ý bằng cách xoá tài khoản; tắt thông báo trong cài đặt điện thoại bất cứ lúc nào.",
      "Khiếu nại hoặc hỏi thêm: liên hệ {contact}.",
    ],
  },
];

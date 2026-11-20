// ESLint (flat config) cho Next 16. Quy tắc định dạng để Prettier lo (eslint-config-prettier tắt
// các quy tắc ESLint xung đột). Xem docs/CONVENTIONS.md.
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettier from "eslint-config-prettier/flat";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  prettier,
  {
    // Quy tắc kiểu React Compiler (eslint-plugin-react-hooks 7) bắt mẫu "tải dữ liệu rồi setState
    // trong useEffect" có ở ~20 trang. Sửa đồng loạt mà không có test giao diện là rủi ro đổi hành
    // vi, nên tạm để mức cảnh báo; chuyển dần sang dữ liệu từ server / use() (reports/08 §6).
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/immutability": "warn",
    },
  },
  {
    // Tham số không dùng được phép nếu bắt đầu bằng "_" (vd. bỏ `node` của react-markdown).
    rules: {
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);

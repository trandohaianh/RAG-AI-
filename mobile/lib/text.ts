// Câu trả lời của LLM dùng Markdown; React Native <Text> không render được,
// nên đổi về chữ thường dễ đọc thay vì hiện ký tự ** và # thô.
export function markdownToPlain(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "• ");
}

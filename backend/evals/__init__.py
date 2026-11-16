"""FieldOps eval harness: chạy pipeline RAG thật trên golden set và chấm điểm tự động.

- offline: embedding băm cục bộ + bộ sinh trích xuất (không gọi API, chạy trong CI).
- live: Gemini embedding + LLM thật (tốn token; chi phí được ghi lại trong báo cáo).
"""

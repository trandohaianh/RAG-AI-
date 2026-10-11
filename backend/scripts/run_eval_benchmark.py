"""Benchmark đánh giá chất lượng RAG trên bộ câu hỏi kiểm thử thật.

Chạy: cd backend && .venv/Scripts/python.exe -m scripts.run_eval_benchmark

Mỗi câu hỏi được retrieve + generate qua ĐÚNG pipeline production (rag/chain.py),
sau đó chấm bằng 2 phương pháp độc lập:
  1. LLM-as-judge (rag/judge.py)         — Gemini tự chấm faithfulness/relevancy.
  2. Sentence-grounding (rag/grounding.py) — đối chiếu từ vựng câu trả lời với
     context đã retrieve, KHÔNG qua LLM, dùng để kiểm tra chéo điểm judge có
     đáng tin hay không (nếu 2 số lệch nhau quá xa nghĩa là judge có thể lạc quan ảo).

Kết quả ghi ra eval_results.json — dùng số liệu này trên landing page/báo cáo,
không dùng số minh hoạ gõ tay.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import google.generativeai as genai

from config import GEMINI_API_KEY, LLM_MODEL
from rag.chain import _unique_sources
from rag.grounding import sentence_grounding_score
from rag.judge import judge_answer
from rag.prompt import build_gemini_prompt, format_context
from rag.retriever import retrieve

genai.configure(api_key=GEMINI_API_KEY)

# Test user thật đã có tài liệu "Dặc tả.txt" ingest sẵn (bộ dữ liệu khảo sát học sinh).
TEST_USER_ID = 1

TEST_SET = [
    {
        "question": "Dữ liệu trong tài liệu này được thu thập từ đâu?",
        "expect_keywords": ["khảo sát", "học sinh", "trung học"],
    },
    {
        "question": "G3 là gì và có thang điểm từ bao nhiêu đến bao nhiêu?",
        "expect_keywords": ["điểm", "cuối", "20"],
    },
    {
        "question": "Có bao nhiêu sinh viên xuất hiện ở cả hai tập dữ liệu Toán và tiếng Bồ Đào Nha?",
        "expect_keywords": ["382"],
    },
    {
        "question": "famrel đo lường điều gì và thang điểm ra sao?",
        "expect_keywords": ["gia đình", "quan hệ"],
    },
    {
        "question": "Ai là tác giả của nghiên cứu gốc cung cấp bộ dữ liệu này?",
        "expect_keywords": ["cortez", "silva"],
    },
    {
        "question": "traveltime được mã hoá thành mấy mức và ý nghĩa từng mức là gì?",
        "expect_keywords": ["phút", "giờ"],
    },
    {
        "question": "Ngày mai thời tiết Hà Nội thế nào?",
        "expect_keywords": [],  # ngoài phạm vi tài liệu — kỳ vọng hệ thống thừa nhận không biết
        "out_of_scope": True,
    },
]


async def _generate(prompt: str, retries: int = 4) -> str:
    loop = asyncio.get_running_loop()

    def _call() -> str:
        model = genai.GenerativeModel(LLM_MODEL)
        resp = model.generate_content(prompt)
        return resp.text or ""

    for attempt in range(retries):
        try:
            return await loop.run_in_executor(None, _call)
        except Exception as exc:  # ResourceExhausted (429) trên free tier
            wait = getattr(getattr(exc, "retry_delay", None), "seconds", None) or (2 ** attempt) * 5
            if attempt == retries - 1:
                raise
            print(f"  ... lỗi tạm thời ({exc.__class__.__name__}), chờ {wait}s rồi thử lại")
            await asyncio.sleep(wait + 1)
    raise RuntimeError("unreachable")


PARTIAL_PATH = Path(__file__).resolve().parents[2] / "eval_results.partial.json"


async def run() -> dict:
    results = []
    if PARTIAL_PATH.exists():
        results = json.loads(PARTIAL_PATH.read_text(encoding="utf-8"))
        print(f"Tiếp tục từ {len(results)} câu đã chạy trước đó ({PARTIAL_PATH.name}).")

    done_questions = {r["question"] for r in results}
    for case in TEST_SET:
        question = case["question"]
        if question in done_questions:
            continue
        chunks = await retrieve(TEST_USER_ID, question)
        context = format_context(chunks) if chunks else ""
        sources = _unique_sources(chunks) if chunks else []
        prompt = build_gemini_prompt(context, question, None)
        try:
            answer = await _generate(prompt)
        except Exception as exc:
            print(f"Bỏ cuộc giữa chừng do lỗi API: {exc}")
            PARTIAL_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Đã lưu {len(results)} câu đã chạy vào {PARTIAL_PATH.name} — chạy lại script để tiếp tục.")
            break

        judge = await judge_answer(question, context, answer)
        grounding = sentence_grounding_score(answer, context)

        keyword_hits = sum(1 for k in case["expect_keywords"] if k.lower() in answer.lower())
        keyword_total = len(case["expect_keywords"])

        results.append(
            {
                "question": question,
                "out_of_scope": case.get("out_of_scope", False),
                "answer": answer,
                "sources": sources,
                "chunks_retrieved": len(chunks),
                "judge": judge,
                "grounding": {
                    "ratio": grounding["ratio"],
                    "grounded": grounding["grounded"],
                    "total_sentences": grounding["total"],
                },
                "keyword_check": {"hits": keyword_hits, "total": keyword_total},
            }
        )
        print(f"[{len(results)}/{len(TEST_SET)}] {question!r} -> judge={judge} grounding={grounding['ratio']}")

    judge_scores = [r["judge"] for r in results if r["judge"] is not None]
    grounding_ratios = [r["grounding"]["ratio"] for r in results if r["grounding"]["ratio"] is not None]

    def _avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": LLM_MODEL,
        "user_id": TEST_USER_ID,
        "n_questions": len(TEST_SET),
        "methodology": (
            "Mỗi câu hỏi chạy qua đúng pipeline production (retrieve + build_gemini_prompt + "
            "generate_content) rồi chấm bằng 2 phương pháp độc lập: (1) LLM-as-judge — Gemini "
            "tự chấm faithfulness/relevancy 0-1; (2) sentence-grounding — đối chiếu từ vựng câu "
            "trả lời với context đã retrieve theo Jaccard overlap, không qua LLM, dùng để kiểm "
            "tra chéo xem điểm judge có lạc quan ảo hay không."
        ),
        "avg_faithfulness": _avg([j["faithfulness"] for j in judge_scores]),
        "avg_relevancy": _avg([j["relevancy"] for j in judge_scores]),
        "avg_sentence_grounding_ratio": _avg(grounding_ratios),
        "results": results,
    }
    return report


if __name__ == "__main__":
    report = asyncio.run(run())
    out_path = Path(__file__).resolve().parents[2] / "eval_results.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["n_questions"] == len(report["results"]) and PARTIAL_PATH.exists():
        PARTIAL_PATH.unlink()
    print(f"\nĐã ghi báo cáo: {out_path}")
    print(f"avg_faithfulness={report['avg_faithfulness']}")
    print(f"avg_relevancy={report['avg_relevancy']}")
    print(f"avg_sentence_grounding_ratio={report['avg_sentence_grounding_ratio']}")

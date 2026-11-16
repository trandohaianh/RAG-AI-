"""Giả lập CRM/NMS gửi sự kiện vào FieldOps (demo / kiểm thử tích hợp).

    python backend/scripts/simulate_integration.py --url <inbound_url> --secret <khoá> --kind nms --count 5

Mỗi sự kiện được ký đúng như hệ thống thật phải ký: HMAC-SHA256(secret, "<timestamp>.<body>").
NMS: phát cảnh báo rồi (tuỳ chọn --clear) gửi alarm.cleared; CRM: tạo ticket, cập nhật, đóng.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import random
import time
import urllib.request
import uuid

SITES = [("Ngõ 165 Cầu Giấy", 21.0336, 105.7949), ("12 Láng Hạ", 21.0156, 105.8131), ("55 Bà Triệu", 21.0182, 105.8497),
         ("8 Phạm Hùng", 21.0331, 105.7818), ("200 Nguyễn Trãi", 20.9939, 105.8076), ("1 Hoàng Quốc Việt", 21.0465, 105.8020)]


def send(url: str, secret: str, event: dict) -> dict:
    body = json.dumps(event, ensure_ascii=False).encode()
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-FieldOps-Timestamp": ts, "X-FieldOps-Signature": sig})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--kind", choices=["crm", "nms"], default="nms")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--clear", action="store_true", help="NMS: gửi alarm.cleared sau khi phát")
    args = parser.parse_args()
    for n in range(args.count):
        address, lat, lng = random.choice(SITES)
        if args.kind == "nms":
            ref = f"OLT-HN{random.randint(1, 9):02d}/PON{random.randint(1, 16)}"
            event = {"event_id": uuid.uuid4().hex, "type": "alarm.raised", "external_ref": ref,
                     "data": {"title": f"Mất tín hiệu {ref}", "description": f"{random.randint(4, 64)} thuê bao mất kết nối",
                              "severity": random.choice(["critical", "major", "minor"]), "address": address,
                              "latitude": lat, "longitude": lng}}
        else:
            ref = f"TCK-{random.randint(1000, 9999)}"
            event = {"event_id": uuid.uuid4().hex, "type": "ticket.created", "external_ref": ref,
                     "data": {"title": random.choice(["Khách báo mất mạng", "Wifi chập chờn", "Modem không lên nguồn"]),
                              "description": "Khách gọi tổng đài báo lỗi", "priority": random.choice(["high", "normal"]),
                              "customer": f"KH {address}", "address": address, "latitude": lat, "longitude": lng}}
        print(event["type"], ref, send(args.url, args.secret, event))
        if args.kind == "nms" and args.clear:
            print("alarm.cleared", ref, send(args.url, args.secret, {"event_id": uuid.uuid4().hex, "type": "alarm.cleared",
                                                                     "external_ref": ref, "data": {}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

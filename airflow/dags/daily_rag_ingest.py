"""
DAG: ingest tài liệu RAG hàng ngày (Medallion + Chroma + GE validation).

Schedule: 02:00 UTC mỗi ngày.
CV bullet: "Orchestrated daily document ETL with Apache Airflow (Bronze/Silver/Gold)."

Đây là phương án THAY THẾ cho container `pipeline` (scheduler tối giản trong deploy/cloud).
Đừng bật cả hai: chuỗi vận hành sẽ chạy hai lần mỗi ngày. Repo không kèm image Airflow;
đặt FIELDOPS_BACKEND_DIR trỏ tới thư mục backend trên worker Airflow.
"""
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

default_args = {
    "owner": "rag-de",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}
BACKEND_DIR = os.environ.get("FIELDOPS_BACKEND_DIR", "/opt/rag/backend")

with DAG(
    dag_id="daily_rag_document_ingest",
    default_args=default_args,
    description="Daily crawl uploads → Bronze/Silver/Gold → Chroma index",
    schedule_interval="0 2 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["rag", "medallion", "de"],
) as dag:
    start = EmptyOperator(task_id="start")

    run_ingest = BashOperator(
        task_id="run_medallion_and_chroma_ingest",
        # daily_ingest giờ thoát khác 0 khi mọi tệp đều lỗi, nên retries của Airflow có tác dụng.
        bash_command=f"cd {BACKEND_DIR} && python -m pipeline.jobs.daily_ingest",
    )

    # KPI → kiểm tra chất lượng dữ liệu → retention → backup (pipeline/jobs/runner.py).
    # Không retry cả chuỗi: một job lỗi (vd. retention) sẽ làm chạy lại cả backup.
    ops_chain = BashOperator(
        task_id="run_ops_pipeline",
        bash_command=f"cd {BACKEND_DIR} && python -m pipeline.jobs.runner",
        retries=0,
    )

    end = EmptyOperator(task_id="end")

    start >> run_ingest >> ops_chain >> end

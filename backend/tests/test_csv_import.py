"""CSV 导入测试：编码、校验、去重、确认流程。"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.utils.csv_tools import auto_mapping, decode_bytes, parse_csv

VALID_CSV = """cash_key,title,direction,amount,event_time,state,source_label,note
TX-0001,门店销售收款,inflow,1234.50,2025-10-03 18:30,scheduled,收银系统,当日营业款
TX-0002,供应商货款,outflow,860.00,2025-10-04 10:00,scheduled,采购单,
"""

PLAN_CSV = """cash_key,title,direction,amount,scheduled_at,state,source_label,note
PLAN-0001,门店租金,outflow,4500.00,2025-10-05 09:00,scheduled,租赁合同,季度付款
"""


def upload(client: TestClient, content: bytes, *, name: str = "test.csv", file_type: str = "transaction"):
    return client.post(
        "/api/v1/imports/csv/preview",
        files={"file": (name, io.BytesIO(content), "text/csv")},
        data={"file_type": file_type},
    )


class TestParsing:
    def test_utf8(self):
        parsed = parse_csv(VALID_CSV.encode("utf-8"))
        assert parsed.encoding in ("utf-8", "utf-8-sig")
        assert len(parsed.rows) == 2
        assert "title" in auto_mapping(parsed.columns)

    def test_utf8_bom(self):
        parsed = parse_csv(("\ufeff" + VALID_CSV).encode("utf-8"))
        assert parsed.encoding == "utf-8-sig"
        assert parsed.columns[0].strip() == "cash_key"
        assert len(parsed.rows) == 2

    def test_gb18030(self):
        parsed = parse_csv(VALID_CSV.encode("gb18030"))
        assert parsed.encoding in ("gb18030", "gbk")
        first = parsed.rows[0][1]
        assert "门店销售收款" in first.values()

    def test_chinese_header_mapping(self):
        csv_text = "编号,名称,方向,金额,预计时间,状态\nA1,门店租金,支出,3000,2025-10-06 09:00,计划中\n"
        parsed = parse_csv(csv_text.encode("utf-8"))
        mapping = auto_mapping(parsed.columns)
        assert mapping["cash_key"] == "编号"
        assert mapping["title"] == "名称"
        assert mapping["direction"] == "方向"
        assert mapping["amount"] == "金额"
        assert mapping["scheduled_at"] == "预计时间"

    def test_tab_delimited(self):
        parsed = parse_csv("title\tdirection\tamount\n租金\toutflow\t100\n".encode("utf-8"))
        assert parsed.delimiter == "\t"
        assert len(parsed.rows) == 1

    def test_empty_file_rejected(self):
        from app.utils.csv_tools import CsvParseError

        with pytest.raises(CsvParseError):
            parse_csv(b"")

    def test_decode_rejects_garbage(self):
        text, encoding = decode_bytes("金额,方向\n100,收入\n".encode("gb18030"))
        assert "金额" in text
        assert encoding


class TestUploadWorkflow:
    def test_upload_does_not_write_events(self, merchant_client: TestClient):
        response = upload(merchant_client, VALID_CSV.encode("utf-8"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_rows"] == 2
        assert body["valid_rows"] == 2
        assert body["can_commit"] is True

        # 尚未写入事项
        assert merchant_client.get("/api/v1/cash-events").json()["meta"]["total"] == 0

    def test_commit_writes_events_with_source(self, merchant_client: TestClient):
        preview = upload(merchant_client, VALID_CSV.encode("utf-8")).json()
        response = merchant_client.post(
            "/api/v1/imports/csv/commit", json={"batch_id": preview["batch_id"]}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["created"] == 2

        events = merchant_client.get("/api/v1/cash-events").json()
        assert events["meta"]["total"] == 2
        event = events["items"][0]
        assert event["source_type"] == "csv_import"
        assert event["source"]["file_name"] == "test.csv"

        detail = merchant_client.get(f"/api/v1/cash-events/{event['id']}/source").json()
        assert detail["source_record"] is not None
        assert "CSV 第 1 行" in (detail["source_record"]["raw_content"] or "")
        assert detail["source_record"]["row_number"] == 1
        assert detail["source_record"]["import_batch_id"] == preview["batch_id"]

    def test_commit_twice_rejected(self, merchant_client: TestClient):
        preview = upload(merchant_client, VALID_CSV.encode("utf-8")).json()
        assert merchant_client.post(
            "/api/v1/imports/csv/commit", json={"batch_id": preview["batch_id"]}
        ).status_code == 200
        second = merchant_client.post(
            "/api/v1/imports/csv/commit", json={"batch_id": preview["batch_id"]}
        )
        assert second.status_code == 409

    def test_duplicate_upload_marks_rows_as_skipped(self, merchant_client: TestClient):
        first = upload(merchant_client, VALID_CSV.encode("utf-8")).json()
        merchant_client.post("/api/v1/imports/csv/commit", json={"batch_id": first["batch_id"]})

        second = upload(merchant_client, VALID_CSV.encode("utf-8")).json()
        assert second["duplicate_rows"] == 2
        result = merchant_client.post(
            "/api/v1/imports/csv/commit", json={"batch_id": second["batch_id"]}
        ).json()
        assert result["created"] == 0
        assert result["skipped"] == 2

    def test_unsupported_extension_rejected(self, merchant_client: TestClient):
        response = upload(merchant_client, VALID_CSV.encode("utf-8"), name="data.xlsx")
        assert response.status_code == 422
        assert response.json()["code"] == "UNSUPPORTED_FILE_EXTENSION"

    def test_empty_upload_rejected(self, merchant_client: TestClient):
        response = upload(merchant_client, b"")
        assert response.status_code == 422


class TestValidation:
    def test_missing_columns(self, merchant_client: TestClient):
        csv_text = "name,direction\n门店租金,outflow\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert body["missing_columns"]
        assert body["can_commit"] is False

    def test_empty_amount(self, merchant_client: TestClient):
        csv_text = "title,direction,amount,scheduled_at\n租金,outflow,,2025-10-05 09:00\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert body["invalid_rows"] == 1
        assert any(item["code"] == "MISSING_AMOUNT" for item in body["issues"])
        assert body["can_commit"] is False

    def test_invalid_amount(self, merchant_client: TestClient):
        csv_text = "title,direction,amount,scheduled_at\n租金,outflow,abc,2025-10-05 09:00\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert any(item["code"] == "INVALID_AMOUNT" for item in body["issues"])

    def test_invalid_time(self, merchant_client: TestClient):
        csv_text = "title,direction,amount,scheduled_at\n租金,outflow,100,不是日期\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert any(item["code"] == "INVALID_TIME" for item in body["issues"])

    def test_invalid_state(self, merchant_client: TestClient):
        csv_text = "title,direction,amount,scheduled_at,state\n租金,outflow,100,2025-10-05 09:00,未知状态\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert any(item["code"] == "INVALID_STATE" for item in body["issues"])

    def test_invalid_direction(self, merchant_client: TestClient):
        csv_text = "title,direction,amount,scheduled_at\n租金,侧向,100,2025-10-05 09:00\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert any(item["code"] == "INVALID_DIRECTION" for item in body["issues"])

    def test_duplicate_within_file(self, merchant_client: TestClient):
        csv_text = (
            "cash_key,title,direction,amount,scheduled_at\n"
            "DUP-1,租金,outflow,100,2025-10-05 09:00\n"
            "DUP-1,租金2,outflow,200,2025-10-06 09:00\n"
        )
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert any(item["code"] == "DUPLICATE_IN_FILE" for item in body["issues"])

    def test_negative_amount_rejected(self, merchant_client: TestClient):
        csv_text = "title,direction,amount,scheduled_at\n租金,outflow,-100,2025-10-05 09:00\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert any(item["code"] == "NEGATIVE_AMOUNT" for item in body["issues"])

    def test_commit_blocked_when_errors_exist(self, merchant_client: TestClient):
        csv_text = "title,direction,amount,scheduled_at\n租金,outflow,,2025-10-05 09:00\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        response = merchant_client.post(
            "/api/v1/imports/csv/commit", json={"batch_id": body["batch_id"]}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "IMPORT_HAS_ERRORS"
        assert merchant_client.get("/api/v1/cash-events").json()["meta"]["total"] == 0


class TestRemap:
    def test_chinese_headers_are_auto_mapped(self, merchant_client: TestClient):
        """常见中文表头（名称 / 方向 / 金额 / 时间）应被自动识别。"""
        csv_text = "名称,方向,金额,时间\n门店租金,outflow,3000,2025-10-06 09:00\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert body["missing_columns"] == []
        assert body["valid_rows"] == 1
        assert body["can_commit"] is True

    def test_remap_fixes_missing_column(self, merchant_client: TestClient):
        csv_text = "款项说明,收付标志,发生金额,计划日期\n门店租金,outflow,3000,2025-10-06 09:00\n"
        body = upload(merchant_client, csv_text.encode("utf-8")).json()
        assert body["missing_columns"]
        assert body["can_commit"] is False

        mapping = {
            "title": "款项说明",
            "direction": "收付标志",
            "amount": "发生金额",
            "scheduled_at": "计划日期",
        }
        remapped = merchant_client.post(
            "/api/v1/imports/csv/remap", json={"batch_id": body["batch_id"], "mapping": mapping}
        ).json()
        assert remapped["missing_columns"] == []
        assert remapped["valid_rows"] == 1
        assert remapped["can_commit"] is True

    def test_remap_to_wrong_column_creates_errors(self, merchant_client: TestClient):
        body = upload(merchant_client, VALID_CSV.encode("utf-8")).json()
        remapped = merchant_client.post(
            "/api/v1/imports/csv/remap",
            json={
                "batch_id": body["batch_id"],
                "mapping": {"title": "note", "direction": "state", "amount": "source_label"},
            },
        ).json()
        assert remapped["can_commit"] is False


class TestPaymentPlanImport:
    def test_payment_plan_uses_scheduled_at(self, merchant_client: TestClient):
        body = upload(merchant_client, PLAN_CSV.encode("utf-8"), file_type="payment_plan").json()
        assert body["valid_rows"] == 1
        assert body["file_type"] == "payment_plan"
        result = merchant_client.post(
            "/api/v1/imports/csv/commit", json={"batch_id": body["batch_id"]}
        ).json()
        assert result["created"] == 1

    def test_templates_endpoint(self, merchant_client: TestClient):
        body = merchant_client.get("/api/v1/imports/csv/templates").json()
        assert "cash_key,title,direction,amount,event_time" in body["transaction"]
        assert "GB18030" in body["encodings"]


class TestImportPermissions:
    def test_upload_requires_merchant(self, client: TestClient):
        from tests.conftest import login, register

        register(client, username="consult_x", roles=["consultant"])
        client.cookies.clear()
        login(client, username="consult_x")
        response = upload(client, VALID_CSV.encode("utf-8"))
        assert response.status_code == 403

    def test_unauthenticated_upload_rejected(self, client: TestClient):
        client.cookies.clear()
        assert upload(client, VALID_CSV.encode("utf-8")).status_code == 401

    def test_other_merchant_cannot_commit_foreign_batch(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        preview = upload(merchant_client, VALID_CSV.encode("utf-8")).json()
        response = second_merchant_client.post(
            "/api/v1/imports/csv/commit", json={"batch_id": preview["batch_id"]}
        )
        assert response.status_code == 404

    def test_other_merchant_cannot_remap_foreign_batch(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        preview = upload(merchant_client, VALID_CSV.encode("utf-8")).json()
        response = second_merchant_client.post(
            "/api/v1/imports/csv/remap",
            json={"batch_id": preview["batch_id"], "mapping": {"title": "title"}},
        )
        assert response.status_code == 404


class TestPathSafety:
    def test_uploaded_filename_is_not_used_as_disk_path(self, merchant_client: TestClient):
        """即使文件名包含路径穿越片段，也只使用随机磁盘文件名。"""
        response = upload(
            merchant_client, VALID_CSV.encode("utf-8"), name="../../evil.csv"
        )
        assert response.status_code in (200, 422)
        if response.status_code == 200:
            import os
            from pathlib import Path

            from app.core.config import settings

            for item in settings.upload_path.iterdir():
                assert ".." not in item.name
                assert os.sep not in item.name
                assert Path(item.name).name == item.name

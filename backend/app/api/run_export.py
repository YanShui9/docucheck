"""Build snapshot-bound, formula-free downloads from verified Run checks."""

import io
import json
import re

from openpyxl import Workbook
from sqlalchemy import Engine, select

from ..db.api_queries import ApiQueryError
from ..db.models import document_versions, evidence_blocks

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAX_CELL_CHARS = 32_767


def export_payload(engine: Engine, result: dict) -> dict:
    """Resolve only evidence cited by the already verified Run snapshot."""
    if not result["checks"]:
        raise ApiQueryError("checks_unavailable")
    evidence_ids = {
        evidence_id
        for check in result["checks"]
        for key in ("evidence_refs", "reason_evidence_refs")
        for evidence_id in check[key]
    }
    sources = []
    if evidence_ids:
        ordered_ids = sorted(evidence_ids)
        with engine.connect() as connection:
            for offset in range(0, len(ordered_ids), 500):
                batch = ordered_ids[offset : offset + 500]
                rows = connection.execute(
                    select(
                        evidence_blocks.c.id,
                        evidence_blocks.c.document_version_id,
                        evidence_blocks.c.source_text,
                        evidence_blocks.c.page_idx,
                        evidence_blocks.c.sheet,
                        evidence_blocks.c.cell,
                        document_versions.c.original_name,
                    )
                    .join(
                        document_versions,
                        evidence_blocks.c.document_version_id == document_versions.c.id,
                    )
                    .where(
                        document_versions.c.case_id == result["case_id"],
                        document_versions.c.snapshot_id == result["snapshot_id"],
                        evidence_blocks.c.id.in_(batch),
                    )
                ).all()
                sources.extend(
                    {
                        "evidence_id": row.id,
                        "document_id": row.document_version_id,
                        "document_name": row.original_name,
                        "page": row.page_idx + 1 if row.page_idx is not None else None,
                        "sheet": row.sheet,
                        "cell": row.cell,
                        "source_text": row.source_text,
                    }
                    for row in rows
                )
        if {source["evidence_id"] for source in sources} != evidence_ids:
            raise ApiQueryError("memory_scope")
    if result.get("checklist_version_id"):
        from ..db.dynamic_repository import DynamicRepository

        # Recheck after the separate source read: metadata/Case/corrections may
        # have changed while the download was being built.
        dynamic = DynamicRepository(engine)
        if dynamic.checks(result["run_id"]) != result:
            raise ApiQueryError("run_changed")
        _, _, repo = dynamic.verified(result["run_id"])
        verified_sources = {
            b.evidence_id: {
                "source_text": b.text,
                "document_id": b.document_id,
                "document_name": repo.source_name(b.document_id),
                "page": b.page_idx + 1 if b.page_idx is not None else None,
                "sheet": b.sheet,
                "cell": b.cell,
            }
            for b in repo.read(list(evidence_ids))
        }
        if any(
            any(
                source[k] != value
                for k, value in verified_sources[source["evidence_id"]].items()
            )
            for source in sources
        ):
            raise ApiQueryError("dynamic_source_changed")
    return {
        "schema_version": 1,
        "result": result,
        "sources": sorted(sources, key=lambda item: item["evidence_id"]),
    }


def render_json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _text(sheet, row: int, column: int, value: object) -> None:
    """Force every cell that may contain external text to an XLSX string."""
    content = "" if value is None else str(value)
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = _ILLEGAL_XML.sub("\ufffd", content)
    if len(content) > _MAX_CELL_CHARS:
        raise ApiQueryError("xlsx_cell_limit")
    cell = sheet.cell(row=row, column=column, value=content)
    cell.data_type = "s"
    cell.number_format = "@"


def render_xlsx(payload: dict) -> bytes:
    workbook = Workbook()
    info = workbook.active
    info.title = "运行信息"
    checks = workbook.create_sheet("核验结果")
    sources = workbook.create_sheet("原文来源")
    result = payload["result"]

    _text(info, 1, 1, "字段")
    _text(info, 1, 2, "值")
    for row, (label, value) in enumerate(
        (
            ("Run ID", result["run_id"]),
            ("Case ID", result["case_id"]),
            ("快照 ID", result["snapshot_id"]),
            ("资料版本", result["revision"]),
            ("运行状态", result["run_status"]),
            ("导出时为当前版本", "是" if result["is_current"] else "否"),
            (
                "说明",
                "每项是否正式以核验结果的正式列为准；历史和部分结果不作为当前正式结论。",
            ),
            ("原文换行", "XLSX 将回车换行统一显示为换行；JSON 保留原始字符。"),
        ),
        start=2,
    ):
        _text(info, row, 1, label)
        _text(info, row, 2, value)
    if result.get("checklist_version_id"):
        _text(info, 10, 1, "清单版本")
        _text(info, 10, 2, result["checklist_version_id"])

    headers = (
        "供应商",
        "指标",
        "处理状态",
        "业务结果",
        "原因代码",
        "规则版本",
        "正式",
        "来源 ID",
        "原因来源 ID",
        "事实 ID",
        "核验结果 ID",
    )
    if result.get("checklist_version_id"):
        headers += ("品目 ID", "指标 ID", "清单版本", "品目")
    for column, label in enumerate(headers, start=1):
        _text(checks, 1, column, label)
    for row, check in enumerate(result["checks"], start=2):
        values = (
            check["supplier"],
            check.get("criterion_name") or check["field_key"],
            check["processing_status"],
            check["business_status"],
            check["reason_code"],
            check["rule_version"],
            "是" if check["is_formal"] else "否",
            "\n".join(check["evidence_refs"]),
            "\n".join(check["reason_evidence_refs"]),
            "\n".join(check["fact_ids"]),
            check["check_result_id"],
        )
        if result.get("checklist_version_id"):
            values += (
                check.get("item_id"),
                check.get("criterion_id"),
                result["checklist_version_id"],
                check.get("item_name"),
            )
        for column, value in enumerate(values, start=1):
            _text(checks, row, column, value)

    source_headers = (
        "来源 ID",
        "文档 ID",
        "原件文件名",
        "PDF 页码",
        "工作表",
        "单元格",
        "原文",
    )
    for column, label in enumerate(source_headers, start=1):
        _text(sources, 1, column, label)
    for row, source in enumerate(payload["sources"], start=2):
        values = (
            source["evidence_id"],
            source["document_id"],
            source["document_name"],
            source["page"],
            source["sheet"],
            source["cell"],
            source["source_text"],
        )
        for column, value in enumerate(values, start=1):
            _text(sources, row, column, value)

    info.column_dimensions["A"].width = 23
    info.column_dimensions["B"].width = 72
    checks.freeze_panes = "A2"
    checks.auto_filter.ref = checks.dimensions
    sources.freeze_panes = "A2"
    sources.auto_filter.ref = sources.dimensions
    for sheet in (checks, sources):
        sheet.column_dimensions["A"].width = 31
        sheet.column_dimensions["B"].width = 31
        sheet.column_dimensions["C"].width = 26
        sheet.column_dimensions["D"].width = 20
        sheet.column_dimensions["G"].width = 45
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()

"""Independently compare every XLSX value with its snapshot-bound JSON export.

This oracle deliberately does not call the application's export renderer. JSON
retains raw source characters; XLSX normalizes CR/LF and replaces characters XML
cannot represent, as documented by the download's running-information sheet.
"""

import json
import re
from io import BytesIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

_XML_CONTROLS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
_SOURCE_KEYS = ('evidence_id', 'document_id', 'document_name', 'page', 'sheet', 'cell', 'source_text')


def _cell_text(value):
    """Empty XLSX string cells are read back as None, including source nulls."""
    text = '' if value is None else str(value)
    text = _XML_CONTROLS.sub('\ufffd', text.replace('\r\n', '\n').replace('\r', '\n'))
    if len(text) > 32_767:
        raise ValueError('cell_limit')
    return text or None


def _expected_sheets(export):
    """Build the public download contract as tables, without rendering an XLSX."""
    result = export['result']
    dynamic = bool(result.get('checklist_version_id'))
    info = [
        ('字段', '值'), ('Run ID', result['run_id']), ('Case ID', result['case_id']),
        ('快照 ID', result['snapshot_id']), ('资料版本', result['revision']),
        ('运行状态', result['run_status']), ('导出时为当前版本', '是' if result['is_current'] else '否'),
        ('说明', '每项是否正式以核验结果的正式列为准；历史和部分结果不作为当前正式结论。'),
        ('原文换行', 'XLSX 将回车换行统一显示为换行；JSON 保留原始字符。'),
    ]
    if dynamic:
        info.append(('清单版本', result['checklist_version_id']))
    check_headers = ('供应商', '指标', '处理状态', '业务结果', '原因代码', '规则版本', '正式',
                     '来源 ID', '原因来源 ID', '事实 ID', '核验结果 ID')
    if dynamic:
        check_headers += ('品目 ID', '指标 ID', '清单版本', '品目')
    check_rows = [check_headers]
    for check in result['checks']:
        row = (check['supplier'], check.get('criterion_name') or check['field_key'],
               check['processing_status'], check['business_status'], check['reason_code'],
               check['rule_version'], '是' if check['is_formal'] else '否',
               '\n'.join(check['evidence_refs']), '\n'.join(check['reason_evidence_refs']),
               '\n'.join(check['fact_ids']), check['check_result_id'])
        if dynamic:
            row += (check.get('item_id'), check.get('criterion_id'),
                    result['checklist_version_id'], check.get('item_name'))
        check_rows.append(row)
    source_rows = [('来源 ID', '文档 ID', '原件文件名', 'PDF 页码', '工作表', '单元格', '原文')]
    source_rows.extend(tuple(source[key] for key in _SOURCE_KEYS) for source in export['sources'])
    tables = {'运行信息': info, '核验结果': check_rows, '原文来源': source_rows}

    attribution = export.get('item_attribution')
    if attribution is not None:
        version = attribution['version']
        entries = [(label, version[key]) for label, key in (
            ('对应版本', 'id'), ('摘要', 'digest'), ('确认人', 'confirmed_by'),
            ('确认时间', 'confirmed_at'), ('原因', 'reason'))]
        entries.append(('说明', attribution['notice']))
        entries.append(('文档ID', '品目ID', '标识类型', '供应商SKU', '确认范围', '锚点ID', '锚点摘录', 'SKU锚点'))
        for entry in version['payload']['entries']:
            entries.append((entry['document_id'], entry['item_id'], entry['identifier_class'],
                            entry.get('supplier_sku'), json.dumps(entry['selector'], ensure_ascii=False),
                            '\n'.join(entry['anchor']['evidence_refs']), entry['anchor']['quote'],
                            json.dumps(entry.get('sku_anchor'), ensure_ascii=False)))
        tables['品目对应依据'] = entries
        tables['对应锚点原文'] = [
            ('来源ID', '文档ID', '文件名', '页码', '工作表', '单元格', '完整原文'),
            *(tuple(anchor[key] for key in _SOURCE_KEYS) for anchor in attribution['anchors']),
        ]
        if attribution.get('exclusions'):
            excluded_rows = [('文档ID', '文件名', '供应商', '页码', '其他型号', '排除原因', '型号锚点',
                              '确认人', '确认时间', '对应版本', '说明', '来源ID', '完整原文')]
            for entry in attribution['exclusions']:
                metadata = tuple(entry[key] for key in (
                    'document_id', 'document_name', 'supplier', 'page', 'other_sku', 'reason'))
                metadata += (entry['sku_anchor']['quote'], entry['confirmed_by'], entry['confirmed_at'],
                             entry['binding_version_id'], '用户确认范围排除；不作为已核验事实。')
                excluded_rows.extend(metadata + (source['evidence_id'], source['source_text'])
                                     for source in entry['sources'])
            tables['范围排除'] = excluded_rows

    for key, sheet_name, source_label in (
        ('material_source_groups', '材料引用分组', '材料来源 ID'),
        ('tax_source_groups', '税口径引用分组', '税说明来源 ID'),
        ('price_source_groups', '单价引用分组', '单价来源 ID'),
    ):
        groups = [(check['check_result_id'], check['criterion_id'], check['item_id'],
                   group['fact_id'], '\n'.join(group['evidence_refs']))
                  for check in result['checks'] for group in check.get(key, [])]
        if groups:
            tables[sheet_name] = [('核验结果 ID', '指标 ID', '品目 ID', '事实 ID', source_label), *groups]
    return tables


def validate_review_artifacts(run, checks, export, binary):
    """Reject mismatched Run identity, JSON results, or any workbook cell/shape.

    The checks response is authoritative for results; export JSON is the source
    of evidence/attribution text for this cross-format consistency check. This
    does not independently attest document truth or query the business service.
    """
    identity = {'run_id': run['id'], 'case_id': run['case_id'], 'snapshot_id': run['snapshot_id'],
                'revision': run['revision'], 'run_status': run['status']}
    if any(checks.get(key) != value for key, value in identity.items()) or checks.get('is_current') is not True:
        raise ValueError('evaluation_result_identity')
    if export.get('result') != checks:
        raise ValueError('evaluation_export_mismatch')
    workbook = None
    try:
        expected = _expected_sheets(export)
        # Normal mode reads actual cells instead of trusting a supplied XLSX
        # dimension declaration, which could otherwise conceal trailing cells.
        workbook = load_workbook(BytesIO(binary), data_only=False, read_only=False)
        if workbook.sheetnames != list(expected):
            raise ValueError('sheets')
        for name, rows in expected.items():
            sheet = workbook[name]
            columns = max(map(len, rows))
            if (sheet.max_row, sheet.max_column) != (len(rows), columns):
                raise ValueError('shape')
            for row_index, values in enumerate(rows, 1):
                for column in range(1, columns + 1):
                    cell = sheet.cell(row_index, column)
                    value = _cell_text(values[column - 1] if column <= len(values) else None)
                    if cell.data_type == 'f' or cell.value != value:
                        raise ValueError('cell')
                    if value is not None and cell.data_type != 's':
                        raise ValueError('cell_type')
    except (BadZipFile, InvalidFileException, ParseError, SyntaxError, KeyError, OSError,
            ValueError, TypeError, IndexError, AttributeError, EOFError):
        raise ValueError('evaluation_xlsx_invalid') from None
    finally:
        if workbook is not None:
            workbook.close()

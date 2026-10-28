"""Render only saved measurements; no missing task is removed from a denominator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def render_markdown(report):
    lines = ['# DocuCheck Agent 评测记录', '',
             '资料为作者可见合成资料，独立封存与独立人工复核均未据此建立。',
             '固定数据集调用真实模型仍属于离线数据集评测；本报告不代表生产流量准确率。', '',
             f"观察来源：`{report.get('observation_origin', 'unknown')}`。", '',
             '| 子集 | 任务数 | 严格通过 | 失败 | 待复核 | 阻塞 | 未执行 |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for split, label in [('development', '开发'), ('measurement', '测量首轮')]:
        r = report[split]
        lines.append(f"| {label} | {r['denominator']} | {r['strict_successes']} | {r['failed']} | "
                     f"{r['pending_review']} | {r['blocked']} | {r['not_executed']} |")
    lines += ['', '## 分层结果', '', '| 子集 | 类别 | 固定分母 | 严格通过 |', '| --- | --- | ---: | ---: |']
    for split in ('development', 'measurement'):
        for category, row in report[split]['categories'].items():
            lines.append(f"| {split} | {category} | {row['denominator']} | {row['strict_successes']} |")
        interval = report[split]['wilson_95']
        if interval is not None:
            lines += ['', f"{split} 首轮严格成功率的 Wilson 95% 区间：{interval[0]:.3%}–{interval[1]:.3%}。"]
    r = report['repeats']
    lines += ['', (f"预选重复任务 {r['planned_tasks']} 题，计划 {r['planned_trials']} trial，"
                   f"已执行 {r['executed_trials']}，三次均严格通过 {r['all_three_successful_tasks']} 题。"),
              '这只是预选题的重复结果，不等于全体任务的 pass^3。工程控制单列。', '',
              '## 逐题结果', '', '| 子集 | task_id | 状态 | 未通过维度 | 待复核维度 |',
              '| --- | --- | --- | --- | --- |']
    for split in ('development', 'measurement'):
        for row in report[split]['tasks']:
            lines.append(f"| {split} | {row['task_id']} | {row['status']} | "
                         f"{', '.join(row['failed_dimensions']) or '—'} | "
                         f"{', '.join(row['pending_dimensions']) or '—'} |")
    lines += ['', '## 重复题逐次结果', '', '| task_id | trial | 状态 |', '| --- | ---: | --- |']
    for row in report['repeats']['trials']:
        lines.append(f"| {row['task_id']} | {row['trial']} | {row['status']} |")
    quality = report['review_quality']
    lines += ['', '## 审查与安全', '',
              (f"审查状态正确 {quality['correct_business_statuses']}/{quality['planned_checks']} 格；"
               f"实际观察 {quality['observed_checks']} 格；危险误判满足 {quality['dangerous_false_satisfied']} 格。"),
              (f"必要证据组覆盖 {quality['matched_groups']}/{quality['necessary_groups']}。"
               '85% 状态、95% 证据和危险误判0是项目目标，并非行业标准。'),
              f"安全出口：`{report['safety_exit']['verdict']}`；缺少观察或复核时不当作已通过。"]
    usage = report['usage']
    lines += ['', '## 用量', '',
              (f"已知请求 {usage['requests']}；输入 {usage['input_tokens']} token；"
              f"输出 {usage['output_tokens']} token；已知未知请求 {usage['unknown_requests']}；"
               f"用量不完整的观察 {usage['observations_with_unknown_usage']}。"),
              'Embedding 请求与实际账单另计，缺失值不作零费用。', '',
              '首轮 Wilson 区间描述此小样本的计数不确定性；分层合成题不代表真实流量分布。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    value = json.loads(args.report.read_bytes())
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(render_markdown(value))


if __name__ == '__main__':
    main()

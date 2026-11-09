"""Summarize completed single-leg CMC jobs on their own evaluated frames."""

import argparse
import csv
import json
from pathlib import Path


def csvwrite(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(folder):
    batch = json.loads((folder/'batch_status.json').read_text(encoding='utf-8-sig'))
    if batch['status'] != 'finished' or len(batch['rows']) != batch['total']:
        raise ValueError('Batch has not finished or is missing case records')
    table, flags, calf = [], [], []
    lines = ['# 单腿 CMC 批次复核', '',
             '按原始 20 N 支撑期映射帧号和百分比。单腿 CMC 由所选运动学轨迹规定骨盆，只加载目标脚力台。', '',
             '| 试次 | 支撑段 | 状态 | 输入支撑帧 | 评估帧范围 | 评估样本 | 原支撑期百分比 | 复核项 |',
             '|---|---|---|---:|---:|---:|---:|---|']
    for r in batch['rows']:
        if r['status']=='completed' and 'selected_mode' in r:
            selected = json.loads((Path(r['output'])/'case_status.json').read_text(encoding='utf-8-sig'))['selected']
            r = {**r,**selected}
        identity = {k:r.get(k) for k in ('trial','stance','side','plate')}
        problems=[]
        def flag(kind, reason):
            problems.append(reason)
            flags.append({**identity,'category':kind,'detail':reason})

        if r['status']=='completed':
            if not r['before_window_initialization']:
                flag('initialization_inside', '初始化占用已核实窗口开头 30 ms')
            for x in r['reserve_flags']:
                flag('reserve',f"{x['coordinate']} 储备 {x['CMC_peak_Nm']:.2f} N·m，占 ID 峰值 {x['CMC_percent_ID']:.1f}%（帧 {x['CMC_peak_frame']}）")
            for x in r['tracking_flags']:
                flag('tracking',f"{x['coordinate']} 追踪 RMS {x['RMS']:.2f}°")
            if r['CMC_saturated_muscles']:
                flag('activation','CMC 激活 ≥99.99%：'+', '.join(r['CMC_saturated_muscles']))
            if r.get('common_SO_missing_frames'):
                flag('SO_gap','旧版共同时间窗内 SO 失败帧 '+', '.join(map(str,r['common_SO_missing_frames'])))
            if r.get('id_reference_status','available')!='available':
                flag('ID_unavailable','ID 未覆盖 CMC 评估窗或参考缺失，储备比例不可评估')
            for x in r['six_muscles']:
                calf.append({**identity,**x})
            phase=r.get('evaluation_phase_percent',r.get('common_phase_percent'))
            native=r['native_window_s']
            evaluated=r.get('evaluation_frames',r.get('common_frames'))
            support=r.get('input_support_frames',r.get('SO_frames'))
            samples=r.get('evaluation_samples',r.get('common_samples'))
            row={**identity,'status':r['status'],'support_first':support[0],'support_last':support[1],
                 'evaluation_first':evaluated[0],'evaluation_last':evaluated[1],'evaluation_samples':samples,
                 'phase_start':phase[0],'phase_end':phase[1],'native_start_s':native[0],'native_end_s':native[1],
                 'review':'; '.join(problems),'output':r['output']}
            lines.append(f"| {r['trial']} | {r['stance']} | 完成 | {support[0]}–{support[1]} | {evaluated[0]}–{evaluated[1]} | "
                         f"{samples} | {phase[0]:.1f}–{phase[1]:.1f}% | {'、'.join(problems) or '—'} |")
        else:
            reason=r.get('reason',r['status']).replace('|','/').replace('\n',' ')
            if r['status'] in ('failed','blocked'):
                flag(r['status'],reason)
            row={**identity,'status':r['status'],'review':reason,'output':r.get('output')}
            lines.append(f"| {r['trial']} | {r['stance']} | {r['status']} | — | — | — | — | {reason} |")
        table.append(row)
    csvwrite(folder/'batch_summary.csv',table,('trial','stance','side','plate','status','support_first','support_last',
             'evaluation_first','evaluation_last','evaluation_samples','phase_start','phase_end','native_start_s','native_end_s','review','output'))
    csvwrite(folder/'review_flags.csv',flags,('trial','stance','side','plate','category','detail'))
    if calf:
        csvwrite(folder/'calf_metrics.csv',calf,tuple(calf[0]))
    status='，'.join(f'{k} {v}' for k,v in batch['counts'].items())
    lines[2:2]=[f'批次状态 {status}。评估样本合计 {sum(x.get("evaluation_samples",0) for x in table)}。','']
    lines.extend(['','本批标记了储备达到同窗 ID 峰值 10% 的坐标，及踝/距下 RMS 大于 1°、其余坐标大于 2° 的追踪项。'
                  '这些筛查和激活触顶分别记录。ID 峰值 ≤1 N·m 时保留绝对储备，不计算比例。'])
    (folder/'CMC批次报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path)
    summarize(parser.parse_args().folder.resolve())

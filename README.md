# OpenSim CMC Skills

`opensim-vicon-cmc` v0.6.0 用于 OpenSim 4.5 下的单腿步态 CMC。使用已核查的 Gait2392／CGM24 原膝关节模型。开跑前确认采用 IK 或 RRA 轨迹，用同一轨迹规定骨盆运动，只加载指定的目标脚力台。

- Fast 和 Slow 分开验收基线，必要时迭代。保留候选、诊断结果、日志和原始输出。
- 每批先确认不新增滤波，或采用四阶 Butterworth 6 Hz 双向零相位低通。可处理运动学、力和力矩，或两者。启用滤波需要 SciPy。
- 每批自动生成 `CMC_quality_dashboard.xlsx`。Fast 和 Slow 各一张表，逐支撑段列出十项质量指标，标记主选模式，并以绿色、黄色、红色和灰色区分数值和 N/A。
- 保留每段最长可核实的求解窗口。保存逐段 RMS／MAX，可另外做同窗 ID 或 SO 对照。

## 使用

在 Codex 中，将本仓库内容安装为 `opensim-vicon-cmc` 技能目录，再调用 `$opensim-vicon-cmc`。直接运行脚本时，需要 OpenSim 4.5 CLI 和 Python API、NumPy、pandas，以及可用的 Node.js 和 `@oai/artifact-tool` 工作区运行时。Excel 导出所需的环境路径在 [Fast／Slow 与 Excel 文档](references/outer-search.md#excel-quality-dashboard) 中说明。

复制[配置示例](assets/prepared-batch.example.json)，填入经核查的输入路径。保持 `outer_search` 为 `true`，预检查后运行批次。

```text
python scripts/run_single_leg_batch.py batch.json --check-only
python scripts/run_single_leg_batch.py batch.json
```

已有 Fast／Slow 结果可单独导出 Excel。

```text
python scripts/export_quality_dashboard.py path/to/batch
```

[技能入口](SKILL.md) · [输入与执行说明](references/execution.md) · [输入滤波和校验记录](references/input-filtering.md) · [Fast／Slow 与 Excel 文档](references/outer-search.md)

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [moduleDir, root, destination, previewDir] = process.argv.slice(2);
const load = createRequire(path.join(path.dirname(moduleDir), 'cmc_dashboard_loader.cjs'));
const { SpreadsheetFile, Workbook } = await import(pathToFileURL(load.resolve('@oai/artifact-tool')).href);
if (!root) process.exit(0);
const read = async (file) => JSON.parse((await fs.readFile(file, 'utf8')).replace(/^\uFEFF/, ''));
const batch = await read(path.join(root, 'batch_status.json'));
assert.equal(batch.status, 'finished');
assert.equal(batch.total, batch.rows.length);
const rows = batch.rows.filter((r) => r.status !== 'excluded_by_config').sort((a, b) =>
  a.trial.localeCompare(b.trial, 'en', { sensitivity: 'base', numeric: true }) || a.stance.localeCompare(b.stance));
const cases = await Promise.all(rows.map(async (row) => {
  if (!row.output || (row.status === 'failed' && !row.quality)) return row;
  const outcome = await read(path.join(row.output, 'case_status.json'));
  assert.equal(outcome.quality, row.quality);
  assert.equal(outcome.selected?.mode ?? null, row.selected_mode);
  return { ...row, case: outcome };
}));

const headings = ['Trial · 脚 / 段 · 主选模式', 'MAX 残余力\n(N)', 'RMS 残余力\n(N)',
  'MAX 残余力矩\n(N·m)', 'RMS 残余力矩\n(N·m)', 'MAX 平移误差\n(cm)', 'RMS 平移误差\n(cm)',
  'MAX 旋转误差\n(°)', 'RMS 旋转误差\n(°)', 'MAX 储备力矩\n(N·m)', 'RMS 储备力矩\n(N·m)'];
const good = [10, 10, 50, 30, 1, 1, 2, 2, 25, 10];
const okay = [25, 25, 75, 50, 2, 2, 5, 5, 50, 25];
const colors = {
  good: { fill: '#C1EDCB', font: { color: '#16582C' } },
  okay: { fill: '#FFEB9C', font: { color: '#7A4B00' } },
  bad: { fill: '#FFC7CE', font: { color: '#8A1820' } },
  na: { fill: '#E8EAED', font: { color: '#5F6673' } },
};
const font = 'Microsoft YaHei';
const first = 11;
const last = first + rows.length - 1;
const workbook = Workbook.create();

function metricValues(selected) {
  if (!selected) return Array(4).fill('N/A');
  assert.equal(selected.status, 'completed');
  assert.equal(selected.tracking.length, 6);
  assert.equal(selected.reserves.length, 6);
  const worst = (items, key) => {
    assert(items.every((item) => Number.isFinite(item[key]) && item[key] >= 0));
    return Math.max(...items.map((item) => item[key]));
  };
  return [worst(selected.tracking, 'peak'), worst(selected.tracking, 'RMS'),
    worst(selected.reserves, 'CMC_peak_Nm'), worst(selected.reserves, 'CMC_RMS_Nm')];
}

function setBand(sheet, address, color, fontColor) {
  const format = sheet.getRange(address).format;
  format.fill = color;
  format.font = { name: font, size: 10, color: fontColor, bold: true };
}

for (const mode of ['Fast', 'Slow']) {
  const key = mode.toLowerCase();
  const sheet = workbook.worksheets.add(mode);
  sheet.tabColor = mode === 'Fast' ? '#294C6B' : '#496E88';
  sheet.showGridLines = false;
  const block = sheet.getRange(`A1:K${Math.max(last, 11)}`);
  block.format.font = { name: font, size: 10, color: '#1C3043' };
  block.format.verticalAlignment = 'center';
  block.format.rowHeightPx = 25;
  sheet.getRange('A:A').format.columnWidthPx = 340;
  sheet.getRange('B:K').format.columnWidthPx = 93;
  sheet.getRange('B7:K10').format.horizontalAlignment = 'center';
  sheet.getRange('A7').format.horizontalAlignment = 'center';
  for (const [row, value] of [
    [1, `单腿 CMC 质量总表 · ${mode}`],
    [2, `${new Set(rows.map((row) => row.trial.toLowerCase())).size} 条试次 · ${rows.length} 个支撑段 · 配置排除 ${batch.rows.length - rows.length} 段`],
    [3, '蓝色试次标签表示本表是主选模式。标签中的诊断表示该模式未通过本批验收。'],
    [4, '灰色 N/A 表示模型不适用或该模式无数值。全身残余和平移追踪不适用于规定骨盆的单腿模型。'],
    [5, '旋转误差和储备力矩各列均取同窗内六个坐标中最大的对应 MAX 或 RMS。颜色采用参考图的绝对阈值，批次验收另含相对储备和联合改善条件。'],
  ]) {
    sheet.mergeCells(`A${row}:K${row}`);
    sheet.getRange(`A${row}`).values = [[value]];
  }
  sheet.getRange('A1').format.font = { name: font, size: 17, bold: true, color: '#173650' };
  sheet.getRange('A1:K1').format.rowHeightPx = 38;
  sheet.getRange('A5:K5').format.rowHeightPx = 28;
  sheet.getRange('A6:K6').format.rowHeightPx = 9;
  sheet.getRange('A7:K10').values = [headings,
    ['GOOD  ≤ 上限', ...good], ['OKAY  > GOOD 且 ≤ 上限', ...okay], ['BAD  > OKAY 上限', ...okay]];
  setBand(sheet, 'A7:K7', '#264560', '#FFFFFF');
  sheet.getRange('A7:K7').format.rowHeightPx = 46;
  sheet.getRange('B7:K7').format.wrapText = true;
  for (const [r, group] of [[8, 'good'], [9, 'okay'], [10, 'bad']]) {
    setBand(sheet, `A${r}:K${r}`, colors[group].fill, colors[group].font.color);
    sheet.getRange(`B${r}:K${r}`).setNumberFormat(r === 10 ? '"> "0' : '"≤ "0');
  }

  const matrix = cases.map((row) => {
    const branch = row.case?.modes[key];
    const status = branch?.selected ? (branch.quality.startsWith('qualified') ? '达标' : '诊断')
      : row.status === 'blocked' ? '受阻' : '无结果';
    const match = row.stance.match(/^stance_(\d+)_[LR]$/);
    const segment = match ? `${row.side}/${match[1]}` : `${row.side}/${row.stance}`;
    const chosen = { fast: 'Fast', slow: 'Slow' }[row.selected_mode] ?? '无';
    return [`${row.trial}  ·  ${segment}  ·  主选 ${chosen}  ·  ${status}`,
      ...Array(6).fill('N/A'), ...metricValues(branch?.selected)];
  });
  if (!matrix.length) {
    sheet.getRange('A11').values = [['没有进入评估的支撑段']];
    continue;
  }
  sheet.getRange(`A${first}:K${last}`).values = matrix;
  sheet.getRange(`A${first}:A${last}`).format.horizontalAlignment = 'left';
  sheet.getRange(`B${first}:K${last}`).format.horizontalAlignment = 'center';
  sheet.getRange(`B${first}:G${last}`).format.fill = colors.na.fill;
  sheet.getRange(`B${first}:G${last}`).format.font = { name: font, size: 10, ...colors.na.font };
  sheet.getRange(`H${first}:K${last}`).setNumberFormat('0.0000');
  for (const column of 'HIJK') {
    const range = sheet.getRange(`${column}${first}:${column}${last}`);
    const cell = `${column}${first}`;
    const lower = `${column}$8`;
    const upper = `${column}$9`;
    range.conditionalFormats.addCustom(`AND(ISNUMBER(${cell}),${cell}>=0,${cell}<=${lower})`, colors.good);
    range.conditionalFormats.addCustom(`AND(ISNUMBER(${cell}),${cell}>${lower},${cell}<=${upper})`, colors.okay);
    range.conditionalFormats.addCustom(`AND(ISNUMBER(${cell}),${cell}>${upper})`, colors.bad);
    range.conditionalFormats.addCustom(`${cell}="N/A"`, colors.na);
  }
  cases.forEach((row, idx) => {
    const rn = idx + first;
    if (row.selected_mode === key) setBand(sheet, `A${rn}`, '#DCEBFA', '#143D60');
    else if (idx % 2) sheet.getRange(`A${rn}`).format.fill = '#F4F7FA';
  });
  sheet.freezePanes.freezeRows(first - 1);
  sheet.freezePanes.freezeColumns(1);
}

workbook.recalculate();
for (const mode of previewDir ? ['Fast', 'Slow'] : []) {
  const preview = await workbook.render({ sheetName: mode, range: 'A1:K19', scale: 1, format: 'png' });
  await fs.writeFile(path.join(previewDir, `${mode}.png`), new Uint8Array(await preview.arrayBuffer()));
}
await fs.mkdir(path.dirname(destination), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(destination);

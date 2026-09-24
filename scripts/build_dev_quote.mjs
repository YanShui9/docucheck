/** Build the one-sheet, fully synthetic supplier quote fixture. */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = path.join(root, "examples", "dev-pack-001");
const previewDir = path.join(root, "tmp", "spreadsheets");
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("报价");
sheet.getRange("A1:B9").values = [
  ["模拟供应商 A 报价单", null],
  ["完全模拟资料 · 仅供开发与测试", null],
  ["供应商", "模拟供应商 A"],
  ["产品型号", "DC-14-A"],
  [null, null],
  ["已装内存", "已装内存 16 GB"],
  ["采购数量", "10 台"],
  ["采购单价", "含税单价 5800 元/台"],
  ["交付期限", "合同签订后 10 个工作日"],
];
sheet.getRange("A1:B9").format.font = { name: "Microsoft YaHei", size: 11 };
sheet.getRange("A1:B1").format = {
  fill: "#17324D",
  font: { name: "Microsoft YaHei", size: 15, bold: true, color: "#FFFFFF" },
};
sheet.getRange("A2:B2").format = {
  fill: "#E7EDF2",
  font: { name: "Microsoft YaHei", size: 10, color: "#4C5B66" },
};
sheet.getRange("A3:A9").format.font = {
  name: "Microsoft YaHei",
  size: 11,
  bold: true,
  color: "#17324D",
};
sheet.getRange("A1:A9").format.columnWidth = 38;
sheet.getRange("B1:B9").format.columnWidth = 34;
sheet.getRange("A1:B9").format.rowHeight = 27;

workbook.recalculate();
const preview = await workbook.render({
  sheetName: "报价",
  range: "A1:B10",
  scale: 2,
  format: "png",
});
await fs.writeFile(
  path.join(previewDir, "vendor-a-quote.png"),
  new Uint8Array(await preview.arrayBuffer()),
);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(outputDir, "vendor-a-quote.xlsx"));

export type TrainingPoint = { updates: number; circle: number; loss: number | null; pim: number | null; res: number | null; lr: number | null };

/** CSV supports quoted commas and escaped quotes; an unfinished last line is not a measurement. */
export function csvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { field += '"'; i++; }
      else quoted = !quoted;
    } else if (c === "," && !quoted) { row.push(field); field = ""; }
    else if (c === "\n" && !quoted) { row.push(field.replace(/\r$/, "")); rows.push(row); row = []; field = ""; }
    else field += c;
  }
  return rows;
}

const number = (value: string | undefined): number | null => value?.trim() && Number.isFinite(Number(value)) ? Number(value) : null;

export function parseTraining(text: string): TrainingPoint[] {
  const [header, ...rows] = csvRows(text.replace(/^\uFEFF/, ""));
  const formats = [
    { circle: "circle", loss: "mean_loss_sum", pim: "train_PIM_native", res: "train_RES_native" },
    { circle: "epoch", loss: "loss_mse", pim: "train_PIM", res: "train_RES" },
  ];
  const format = header && formats.find(columns => ["updates", ...Object.values(columns)].every(key => header.includes(key)));
  if (!format) {
    throw new Error("训练记录尚未写完，或未提供支持的指标列；不推测曲线含义。");
  }
  return rows.filter(row => row.length === header.length).flatMap(row => {
    const get = (key: string): number | null => number(row[header.indexOf(key)]);
    const updates = get("updates"), circle = get(format.circle);
    if (updates === null || circle === null || !Number.isInteger(updates) || updates < 0) return [];
    return [{ updates, circle, loss: get(format.loss), pim: get(format.pim), res: get(format.res), lr: get("lr") }];
  });
}

export function trainingIdentity(path: string): { experiment: string; invocation: string } | null {
  const match = /^local_commands\/([^/]+)\/([^/]+)\/(?:training\/)?training\.csv$/.exec(path);
  return match ? { experiment: match[1], invocation: match[2] } : null;
}

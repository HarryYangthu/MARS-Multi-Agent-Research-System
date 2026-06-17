const fs = require("node:fs");
const path = require("node:path");

const typesDir = path.join(__dirname, "..", ".next", "types");

if (fs.existsSync(typesDir)) {
  for (const entry of fs.readdirSync(typesDir)) {
    if (/ \d+\.ts$/.test(entry)) {
      fs.unlinkSync(path.join(typesDir, entry));
    }
  }
}

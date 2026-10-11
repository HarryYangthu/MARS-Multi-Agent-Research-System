import assert from "node:assert/strict";
import { csvRows, parseTraining, trainingIdentity } from "../src/lib/trainingCharts";

assert.deepEqual(csvRows('a,b\r\n"with,comma","quote""value"\r\nunfinished'), [["a", "b"], ["with,comma", 'quote"value']]);
const source = "circle,updates,lr,mean_loss_sum,train_PIM_native,train_RES_native,centers\r\n"
  + '0,0,.001,,21.3,21.4,"[-27.5, 0]"\r\n25,275,.001,.31,21.3,9.8,"[-27.5, 0]"\r\n50,550,.001,.2';
const points = parseTraining(source);
assert.equal(points.length, 2);
assert.equal(points[0].loss, null);
assert.equal(points[1].updates, 275);
assert.equal(points[1].res, 9.8);
assert.throws(() => parseTraining("step,loss\n1,0.5\n"));
assert.deepEqual(trainingIdentity("local_commands/B/run123/training.csv"), { experiment: "B", invocation: "run123" });
assert.equal(trainingIdentity("local_commands/B/training.csv"), null);
console.log("Training chart CSV checks passed");

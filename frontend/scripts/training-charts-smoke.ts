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
assert.deepEqual(trainingIdentity("local_commands/B/run123/training/training.csv"), { experiment: "B", invocation: "run123" });
assert.equal(trainingIdentity("local_commands/B/run123/unrelated/training.csv"), null);
const sparse = parseTraining("epoch,updates,loss_mse,lr,train_PIM,train_RES,validation_PIM,validation_RES\n1,79,.05,.004,25,22,25,23\n25,1975,.001,.004,25,6,25,5\n50,3950,.0008");
assert.equal(sparse.length, 2);
assert.equal(sparse[1].updates, 1975);
assert.equal(sparse[1].circle, 25);
assert.equal(sparse[1].loss, .001);
assert.equal(sparse[1].res, 6); // Training and validation columns must not be interchanged.
console.log("Training chart CSV checks passed");

import { askPythonHarness } from "./harnessBridge.js";
const decision = await askPythonHarness({
    side: "buyer",
    item: "lamp",
    limit: 120,
    currentAsk: 130,
    transcript: ["lamp: asking 130"],
});
console.log(JSON.stringify(decision));
//# sourceMappingURL=bridge-smoke.js.map
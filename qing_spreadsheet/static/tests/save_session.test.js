import { describe, expect, test } from "@odoo/hoot";
import { SaveSession } from "@qing_spreadsheet/bundle/save_session";
import { Model } from "@odoo/o-spreadsheet";

function storage() {
    const values = new Map();
    return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

describe("Qing workbook persistence", () => {
    test("a lost response recovers without writing twice", async () => {
        let committed = false;
        let writes = 0;
        const call = async (method) => {
            if (method === "save_status") { return { saved: committed, version: 1, current_version: committed ? 1 : 0 }; }
            writes++;
            committed = true;
            throw new Error("response lost");
        };
        const saved = storage();
        const session = new SaveSession({ call, storage: saved, key: "draft", version: 0, token: "lease" });
        try { await session.submit({ sheets: [] }); } catch { /* Simulated network failure. */ }
        const next = new SaveSession({ call, storage: saved, key: "draft", version: 0, token: "lease" });
        await next.retry();
        expect(writes).toBe(1);
        expect(next.version).toBe(1);
        expect(next.pending).toBe(null);
    });

    test("native compressed formulas survive reloading and XLSX conversion", async () => {
        const model = new Model();
        const sheetId = model.getters.getActiveSheetId();
        for (let row = 0; row < 10; row++) {
            model.dispatch("UPDATE_CELL", { sheetId, col: 0, row, content: String(row + 1) });
        }
        model.dispatch("UPDATE_CELL", { sheetId, col: 1, row: 0, content: "=SUM(A1:A10)" });
        const restored = new Model(model.exportData());
        expect(restored.getters.getEvaluatedCell({ sheetId, col: 1, row: 0 }).value).toBe(55);
        const exported = await restored.exportXLSX();
        expect(exported.files.some((file) => file.path === "xl/workbook.xml")).toBe(true);
        const files = Object.fromEntries(exported.files.map((file) => [file.path, file.content]));
        const imported = new Model(files);
        expect(imported.getters.getEvaluatedCell({ sheetId: imported.getters.getActiveSheetId(), col: 1, row: 0 }).value).toBe(55);
        for (const item of [model, restored, imported]) { item.leaveSession({ shouldSnapshot: false }); }
    });
});

import { expect, test } from "@odoo/hoot";
import { advanceTime, animationFrame, mockVibrate } from "@odoo/hoot-mock";
import { contains, defineModels, fields, models, mountWithCleanup, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { BarcodePlugin } from "@barcodes/barcode_plugin";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { WarehouseWorkbench } from "@qing_stock_barcode/workbench";
import { browser } from "@web/core/browser/browser";
import { ScanRequestQueue } from "@qing_stock_barcode/request_queue";

class ScanSession extends models.Model {
    _name = "qing.stock.scan.session";
    name = fields.Char();
}
defineModels([ScanSession]);
defineMailModels();

function memoryStorage() {
    const values = new Map();
    return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value) };
}

test("lost acknowledgement is reconciled with the same UUID after page reload", async () => {
    const storage = memoryStorage();
    let count = 0;
    let request;
    const first = new ScanRequestQueue({ storage, storageKey: "test", call: async (method, args) => {
        request = args[1];
        count++;
        throw new Error("Response lost after commit");
    } });
    try { await first.enqueue(7, { op: "scan", code: "A" }); } catch { /* Expected uncertainty. */ }
    expect(first.paused).toBe(true);
    const restored = new ScanRequestQueue({ storage, storageKey: "test", call: async (method, args) => {
        expect(method).toBe("get_event");
        expect(args[1]).toBe(request);
        return { ok: true, result: {} };
    } });
    await restored.drain(true);
    expect(count).toBe(1);
    expect(restored.items).toHaveLength(0);
    expect(JSON.parse(storage.getItem("test"))).toEqual([]);
});

async function pdaWorkbench() {
    // Android Chrome uses a second hidden input in the native plugin.
    patchWithCleanup(BarcodePlugin, { isMobileChrome: true });
    mockVibrate(() => true);
    const data = { id: 9, name: "WH/IN/42", state: "scanning", kind: "picking", owned: true, manager: false,
        company: "Test warehouse", user: "Operator", picking_id: 42, moves: [], lines: [], events: [] };
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", transfers: [], sessions: [] }));
    onRpc("qing.stock.scan.session", "get_session", () => data);
    const scans = [];
    onRpc("qing.stock.scan.session", "execute", ({ args }) => {
        scans.push({ code: args[2].code, op: args[2].op, key: args[1] });
        return { ok: true, result: { message: "Recorded" }, session: data };
    });
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: { session_id: 9 } } } });
    await contains(".qing_scan_pda_toggle").check();
    await animationFrame();
    return { input: document.querySelector("#qing_barcode_input"), scans, component };
}

async function inject(input, text, composing = false) {
    input.value = text;
    input.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText", isComposing: composing }));
    await animationFrame();
}

test("PDA text injection waits for delimiters and preserves short codes, semicolons and GS1", async () => {
    const { input, scans } = await pdaWorkbench();
    expect(input.getAttribute("inputmode")).toBe("none");
    expect(document.activeElement).toBe(input);
    await inject(input, "A");
    expect(scans).toHaveLength(0);
    await inject(input, "A\r\n");
    await inject(input, "A\t");
    await inject(input, "10LOT;PART\x1d21SN\n");
    expect(scans.map((scan) => scan.code)).toEqual(["A", "A", "10LOT;PART\x1d21SN"]);
    expect(new Set(scans.map((scan) => scan.key)).size).toBe(3);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await animationFrame();
    expect(scans).toHaveLength(3);
});

test("PDA Enter and Tab submit once without native listener duplication", async () => {
    const { input, scans } = await pdaWorkbench();
    for (const key of ["Enter", "Tab"]) {
        await inject(input, "SKU-42");
        const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
        input.dispatchEvent(event);
        await animationFrame();
        expect(event.defaultPrevented).toBe(true);
        expect(document.activeElement).toBe(input);
    }
    expect(scans.map((scan) => scan.code)).toEqual(["SKU-42", "SKU-42"]);
});

test("PDA composition does not submit partial scans and device settings are removed", async () => {
    const { input, scans } = await pdaWorkbench();
    input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    await inject(input, "LOT中文\n", true);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true }));
    expect(scans).toHaveLength(0);
    input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
    await animationFrame();
    expect(scans.map((scan) => scan.code)).toEqual(["LOT中文"]);
    expect(".qing_scan_device_settings").toHaveCount(0);
    await inject(input, "SKU\n");
    expect(scans).toHaveLength(2);
});

test("PDA focus respects editable fields and dialogs and can be restored explicitly", async () => {
    const { input, scans } = await pdaWorkbench();
    await contains("input[type='number']").focus();
    await contains(".qing_scan_session_header h2").click();
    // Explicitly focusing another editable field must not be stolen by background responses.
    const quantity = document.querySelector("input[type='number']");
    quantity.focus();
    await inject(input, "SKU\n");
    expect(document.activeElement).toBe(quantity);
    await contains(".qing_scan_device_toolbar .btn-outline-primary").click();
    expect(document.activeElement).toBe(input);
    await contains(".qing_scan_footer .btn-outline-danger").click();
    await inject(input, "IGNORED\n");
    expect(scans).toHaveLength(1);
    await contains(".modal-header [aria-label=Close]").click();
    await contains(".qing_scan_device_toolbar .btn-outline-primary").click();
    expect(document.activeElement).toBe(input);
});

test("scanning stays disabled until the selected transfer has finished loading", async () => {
    const pending = Promise.withResolvers();
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", sessions: [], transfers: [
        { id: 42, name: "WH/IN/42", source: "Vendors", destination: "Stock" },
    ] }));
    onRpc("qing.stock.scan.session", "start_picking", () => pending.promise);
    await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: {} } } });
    await contains(".qing_scan_task_select").click();
    await contains(".qing_scan_start").click();
    expect("#qing_barcode_input").toHaveCount(0);
    pending.resolve({ id: 9, name: "WH/IN/42", state: "scanning", kind: "picking", owned: true,
        manager: false, company: "Test", user: "Operator", picking_id: 42, moves: [], lines: [], events: [] });
    await animationFrame();
    expect("#qing_barcode_input").toHaveProperty("disabled", false);
});

test("two real scans of the same SKU have distinct identities", async () => {
    const keys = [];
    const queue = new ScanRequestQueue({ storage: memoryStorage(), storageKey: "test", call: async (method, args) => {
        keys.push(args[1]);
        return { ok: true, result: {} };
    } });
    await queue.enqueue(7, { op: "scan", code: "A" });
    await queue.enqueue(7, { op: "scan", code: "A" });
    expect(keys).toHaveLength(2);
    expect(keys[0]).not.toBe(keys[1]);
});

test("a request is never sent when durable identity cannot be saved", async () => {
    let calls = 0;
    const queue = new ScanRequestQueue({
        storage: { getItem: () => null, setItem: () => { throw new Error("Storage unavailable"); } },
        storageKey: "test", call: async () => { calls++; },
    });
    try { await queue.enqueue(7, { op: "scan", code: "A" }); } catch { /* No request may be sent. */ }
    expect(calls).toBe(0);
    expect(queue.items).toHaveLength(0);
});

test("real workbench renders tasks and sends manual barcode through the workbench", async () => {
    mockVibrate(() => true);
    onRpc("qing.stock.scan.session", "dashboard", () => ({
        company: "Test warehouse", transfers: [{ id: 42, name: "WH/IN/42", partner: "Supplier", source: "Vendors", destination: "Stock" }],
        sessions: [], truncated: false,
    }));
    onRpc("qing.stock.scan.session", "start_picking", () => ({
        id: 9, name: "WH/IN/42", state: "scanning", kind: "picking", owned: true, manager: false,
        company: "Test warehouse", user: "Operator", picking_id: 42, moves: [], lines: [], events: [],
    }));
    onRpc("qing.stock.scan.session", "execute", ({ args }) => {
        expect(args[0]).toEqual([9]);
        expect(args[2].code).toBe("SKU-42");
        expect.step("scan");
        return { ok: true, result: { message: "Recorded one item" }, session: {
            id: 9, name: "WH/IN/42", state: "scanning", kind: "picking", owned: true, manager: false,
            company: "Test warehouse", user: "Operator", picking_id: 42, moves: [], lines: [], events: [],
        } };
    });
    await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: {} } } });
    expect(".qing_scan_nav button").toHaveCount(5);
    await contains(".qing_scan_task_select").click();
    await contains(".qing_scan_start").click();
    await contains("#qing_barcode_input").fill("SKU-42", { confirm: false });
    await contains(".qing_scan_input_row button[type='submit']").click();
    expect.verifySteps(["scan"]);
    expect(".qing_scan_feedback").toHaveText("Recorded one item");
});


test("Validate requires readiness and a settled connection, sends one finish without extra confirmation", async () => {
    const { component, scans } = await pdaWorkbench();
    const button = ".qing_scan_validate";
    expect(button).toHaveProperty("disabled", true);
    component.state.session.validation_blocker = false;
    await animationFrame();
    expect(button).toHaveProperty("disabled", false);
    for (const [key, blocked, ready] of [
        ["online", false, true], ["paused", true, false], ["pending", 1, 0],
        ["busy", true, false], ["loading", true, false],
        ["cameraOpen", true, false],
    ]) {
        component.state[key] = blocked;
        await animationFrame();
        expect(button).toHaveProperty("disabled", true);
        await component.validateTransfer();
        expect(scans).toHaveLength(0);
        component.state[key] = ready;
        if (component.recoveryOpen) { await contains(".modal-header [aria-label=Close]").click(); }
    }
    component.state.session.validation_blocker = "Scan the source location first.";
    await animationFrame();
    expect(button).toHaveProperty("disabled", true);
    component.state.session.validation_blocker = false;
    await animationFrame();
    await contains(button).click();
    expect(scans).toHaveLength(1);
    expect(scans[0].op).toBe("finish");
    expect(".modal").toHaveCount(0);
    component.state.session.state = "done";
    await animationFrame();
    expect(".qing_scan_validate").toHaveCount(0);
    expect(".qing_scan_return").toHaveCount(1);
});


test("navigation uses visible SVG icons and progress follows quantities without reaching 100 early", async () => {
    const { component } = await pdaWorkbench();
    expect(".qing_scan_eyebrow").toHaveCount(0);
    expect(".qing_scan_nav button svg path").toHaveCount(5);
    for (const [planned, scanned, percent] of [[10, 0, 0], [10, 5, 50], [10, 10, 100], [1, .9999, 99.9], [10, 15, 100], [0, 1, 0]]) {
        component.state.session.moves = [{ id: 42, product: "Widget", unit: "Units", planned, scanned }];
        await animationFrame();
        expect(".qing_scan_progress_percent").toHaveText(`${percent}%`);
        expect(".qing_scan_progress_track").toHaveAttribute("aria-valuenow", String(percent));
        expect(document.querySelector(".qing_scan_progress").style.getPropertyValue("--qing-progress")).toBe(`${percent}%`);
    }
});


test("inline scan controls submit only nonblank input and feedback precedes the input panel", async () => {
    const { component, input, scans } = await pdaWorkbench();
    expect(".qing_scan_entry .qing_scan_camera").toHaveCount(1);
    expect(".qing_scan_entry .qing_scan_confirm").toHaveProperty("disabled", true);
    expect(".qing_scan_refocus svg").toHaveCount(1);
    expect(".qing_scan_keyboard svg").toHaveCount(1);
    expect(".qing_scan_camera").toHaveProperty("disabled", false);
    await inject(input, "   ");
    expect(".qing_scan_confirm").toHaveProperty("disabled", true);
    component.submitBarcode();
    expect(scans).toHaveLength(0);
    await inject(input, "SKU-42");
    expect(".qing_scan_confirm").toHaveProperty("disabled", false);
    component.state.online = false;
    await animationFrame();
    expect(".qing_scan_confirm").toHaveProperty("disabled", true);
    component.state.online = true;
    await animationFrame();
    await contains(".qing_scan_confirm").click();
    expect(scans.map(scan => scan.code)).toEqual(["SKU-42"]);
    expect(".qing_scan_confirm").toHaveProperty("disabled", true);
    expect(input.value).toBe("");
    const feedback = document.querySelector(".qing_scan_feedback");
    const panel = document.querySelector(".qing_scan_input_panel");
    expect(Boolean(feedback.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
});


test("PDA reconnect focuses the input after it is enabled again", async () => {
    const { input, component } = await pdaWorkbench();
    input.focus();
    component.state.online = false;
    await animationFrame();
    expect(input.disabled).toBe(true);
    input.blur();
    component.state.online = true;
    await animationFrame();
    await animationFrame();
    expect(input.disabled).toBe(false);
    expect(document.activeElement).toBe(input);
});


test("list ignores scans, has row actions only and completed transfers cannot start", async () => {
    const done = { id: 43, name: "WH/IN/43", state: "done", source: "Vendors", destination: "Stock", responsible: "Manager" };
    let transfers = [{ ...done, id: 42, name: "WH/IN/42", state: "assigned" }, done];
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", sessions: [], transfers }));
    onRpc("qing.stock.scan.session", "start_picking", () => { throw new Error("Selection must not claim a transfer"); });
    onRpc("qing.stock.scan.session", "find_picking", () => { throw new Error("List must ignore scanner input"); });
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: {} } } });
    expect(".qing_scan_input_panel").toHaveCount(0);
    expect(".qing_scan_task_row .qing_scan_start").toHaveCount(1);
    expect(".qing_scan_view").toHaveCount(1);
    expect(".qing_scan_task_completed").toHaveCount(1);
    expect(".qing_scan_task_select > span:nth-child(3):eq(0)").toHaveText("Manager");
    await contains(".qing_scan_task_select:eq(0)").click();
    expect(".qing_scan_task_row:eq(0)").toHaveClass("qing_scan_selected");
    await component.onBarcode("WH/IN/42");
    await component.startTransfer(done);
    expect(component.state.session).toBe(null);
    expect(component.state.message).toBe("");
    transfers = [];
    await component.refreshDashboard();
    await animationFrame();
    expect(component.state.selectedTransfer).toBe(false);
});

test("return to list clears scanning hints and lookup keeps its scanner", async () => {
    const { component } = await pdaWorkbench();
    component.state.message = "Scan now";
    component.state.messageType = "danger";
    await contains(".qing_scan_back").click();
    expect(".qing_scan_input_panel").toHaveCount(0);
    expect(".qing_scan_feedback").toHaveCount(0);
    await component.changePage("lookup");
    await animationFrame();
    expect(".qing_scan_input_panel").toHaveCount(1);
    expect(component.canScan).toBe(true);
});

test("large mode dialog blocks scans, applies a mode and restores PDA focus", async () => {
    const { component, input, scans } = await pdaWorkbench();
    await contains(".qing_scan_mode_button").click();
    expect(".qing_scan_mode_choice").toHaveCount(6);
    await inject(input, "IGNORED\n");
    expect(scans).toHaveLength(0);
    await contains(".qing_scan_mode_choice:eq(1)").click();
    await animationFrame();
    await animationFrame();
    expect(component.state.mode).toBe("product");
    expect(document.activeElement).toBe(input);
    await contains(".qing_scan_mode_button").click();
    await contains(".modal-header [aria-label=Close]").click();
    expect(component.state.mode).toBe("product");
});


test("document progress caps each move and details expand only for the clicked move", async () => {
    const { component } = await pdaWorkbench();
    component.state.session.moves = [
        { id: 1, product: "Units product", unit: "Units", planned: 10, scanned: 20 },
        { id: 2, product: "Weight product", unit: "kg", planned: 100, scanned: 0 },
    ];
    component.state.session.lines = [
        { id: 11, move_id: 1, product: "Lot A", quantity: 12 },
        { id: 12, move_id: 1, product: "Lot B", quantity: 8 },
        { id: 21, move_id: 2, product: "Other move", quantity: 0 },
    ];
    await animationFrame();
    expect(".qing_scan_document_progress").toHaveAttribute("aria-valuenow", "50");
    expect(".qing_scan_operation_details").toHaveCount(0);
    await contains(".qing_scan_progress_toggle:eq(0)").click();
    expect(".qing_scan_operation_details .qing_scan_select").toHaveCount(2);
    await contains(".qing_scan_select:eq(0)").click();
    expect(component.state.selectedLine).toBe(11);
    await contains(".qing_scan_progress_toggle:eq(1)").click();
    expect(".qing_scan_operation_details .qing_scan_select").toHaveCount(1);
    expect(".qing_scan_operation_details").toHaveText(/Other move/);
    expect(component.state.selectedLine).toBe(false);
    await contains(".qing_scan_progress_toggle:eq(1)").click();
    expect(".qing_scan_operation_details").toHaveCount(0);
    component.state.session.moves[1].scanned = 100;
    await animationFrame();
    expect(component.documentProgress).toBe(100);
});

test("inventory progress counts confirmed lines including zero and hides baseline until review", async () => {
    const { component } = await pdaWorkbench();
    component.state.session.kind = "inventory";
    component.state.session.lines = [
        { id: 1, product: "Counted zero", quantity: 0, counted: true, baseline: null },
        { id: 2, product: "Not counted", quantity: 0, counted: false, baseline: null },
    ];
    await animationFrame();
    expect(component.documentProgress).toBe(50);
    expect(".qing_scan_operation_details").toHaveCount(0);
    await contains(".qing_scan_progress_toggle:eq(1)").click();
    expect(".qing_scan_operation_details input[type=number]").toHaveCount(1);
    expect(".qing_scan_operation_details").not.toHaveText(/Recorded stock/);
    const data = { ...component.state.session, id: 10 };
    component.setSession(data);
    await animationFrame();
    expect(".qing_scan_operation_details").toHaveCount(0);
});


test("inventory list selects without starting and uses row icons to start or resume", async () => {
    const data = { id: 9, name: "WH/Stock", state: "scanning", kind: "inventory", owned: true,
        company: "Test", user: "Operator", lines: [], events: [] };
    let starts = 0;
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", transfers: [] }));
    onRpc("qing.stock.scan.session", "find_locations", () => [
        { id: 1, name: "WH/Stock", barcode: "STOCK", session_id: 9, progress: 50 },
        { id: 2, name: "WH/Input/Order Processing/Dispatch Zone/Gate A", barcode: "" },
    ]);
    onRpc("qing.stock.scan.session", "get_session", ({ args }) => { expect(args[0]).toEqual([9]); return data; });
    onRpc("qing.stock.scan.session", "start_count", ({ args }) => { starts++; expect(args[0]).toBe(2); return { ...data, id: 10 }; });
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: {} } } });
    await component.changePage("inventory");
    await animationFrame();
    expect(".qing_scan_location").toHaveCount(2);
    expect(".qing_scan_location.qing_scan_card").toHaveCount(0);
    expect(".qing_scan_location_header").toHaveText(/Location\s+Action/);
    await contains(".qing_scan_location_select:eq(0)").click();
    expect(".qing_scan_location:eq(0)").toHaveClass("qing_scan_selected");
    expect(component.state.session).toBe(null);
    expect(starts).toBe(0);
    expect(document.querySelector(".qing_scan_location").style.getPropertyValue("--qing-progress")).toBe("50%");
    await contains(".qing_scan_location .qing_scan_start:eq(0)").click();
    expect(component.state.session.id).toBe(9);
    await component.changePage("inventory");
    await animationFrame();
    expect(component.state.selectedLocation).toBe(false);
    await contains(".qing_scan_location .qing_scan_start:eq(1)").click();
    expect(starts).toBe(1);
    expect(component.state.session.id).toBe(10);
});

test("feedback expires five seconds after the latest message and shows the predecessor", async () => {
    const { component } = await pdaWorkbench();
    component.state.session.backorder_of = "WH/IN/00010";
    component.showMessage("First");
    await animationFrame();
    expect(".qing_scan_backorder_source").toHaveText(/WH\/IN\/00010/);
    await advanceTime(4000);
    component.showMessage("Second", false);
    await advanceTime(1000);
    expect(".qing_scan_feedback").toHaveText("Second");
    await advanceTime(4000);
    expect(".qing_scan_feedback").toHaveCount(0);
    component.showMessage("Dismiss");
    await animationFrame();
    await contains(".qing_scan_feedback .btn-close").click();
    expect(".qing_scan_feedback").toHaveCount(0);
});

test("uncertain scans open one recovery dialog and reconcile the original request", async () => {
    const { component, scans } = await pdaWorkbench();
    const calls = [];
    let fail = true;
    component.queue.call = async (method, args) => {
        calls.push({ method, key: args[1] });
        if (fail) { throw new Error("user is not connected"); }
        return { ok: true, result: { message: "Recovered" }, session: component.state.session };
    };
    await component.command({ op: "scan", code: "SKU" });
    await animationFrame();
    await animationFrame();
    expect(".modal").toHaveCount(1);
    expect(component.canScan).toBe(false);
    expect(".qing_scan_feedback").toHaveCount(0);
    await contains(".modal-footer .btn-primary").click();
    expect(".modal").toHaveCount(1);
    await contains(".modal-header [aria-label=Close]").click();
    expect(".qing_scan_recovery").toHaveCount(1);
    await contains(".qing_scan_recovery").click();
    fail = false;
    await contains(".modal-footer .btn-primary").click();
    expect(".modal").toHaveCount(0);
    expect(calls.map((call) => call.method)).toEqual(["execute", "get_event", "get_event"]);
    expect(new Set(calls.map((call) => call.key)).size).toBe(1);
    expect(component.state.pending).toBe(0);
    expect(scans).toHaveLength(0);
});

test("active progress lives in the transfer row and its icon resumes the same session", async () => {
    const data = { id: 9, name: "WH/IN/42", state: "scanning", kind: "picking", owned: true,
        company: "Test", user: "Operator", picking_id: 42, moves: [], lines: [], events: [] };
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", sessions: [{ id: 9, name: "Old card" }],
        transfers: [{ id: 42, name: data.name, state: "assigned", session_id: 9, progress: 50 }] }));
    onRpc("qing.stock.scan.session", "get_session", ({ args }) => { expect(args[0]).toEqual([9]); return data; });
    onRpc("qing.stock.scan.session", "start_picking", () => { throw new Error("Do not create a new session"); });
    await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: {} } } });
    expect(".o_qing_scan").not.toHaveText(/Resume a session|Old card|50%/);
    expect(".qing_scan_task_row").toHaveClass("qing_scan_task_active");
    expect(document.querySelector(".qing_scan_task_row").style.getPropertyValue("--qing-progress")).toBe("50%");
    await contains(".qing_scan_start").click();
    expect(".qing_scan_session_header h2").toHaveText(data.name);
});


test("completed view uses native readonly details, refreshes and returns with completion filter preserved", async () => {
    const calls = [];
    const done = { id: 43, name: "WH/OUT/43", state: "done", source: "Stock", destination: "Customer" };
    onRpc("qing.stock.scan.session", "dashboard", ({ args }) => {
        calls.push(args);
        return { company: "Test", transfers: args[2] ? [done] : [] };
    });
    let reads = 0;
    onRpc("qing.stock.scan.session", "get_completed_picking", ({ args }) => {
        expect(args).toEqual([43]);
        reads++;
        return { id: false, readonly: true, name: done.name, state: "done", kind: "picking", picking_id: 43,
            picking_state: "done", operation: "outgoing", lines: [], moves: [], events: [] };
    });
    onRpc("qing.stock.scan.session", "start_picking", () => { throw Error("Completed views cannot claim work"); });
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: {} } } });
    await component.changePage("outgoing");
    await animationFrame();
    expect(".qing_scan_task_completed").toHaveStyle({ "--qing-progress": "100%" });
    await contains(".qing_scan_completed_filter input").uncheck();
    expect(".qing_scan_view").toHaveCount(0);
    expect(calls.at(-1)).toEqual(["outgoing", "", false]);
    await contains(".qing_scan_completed_filter input").check();
    await contains(".qing_scan_view").click();
    expect(reads).toBe(1);
    expect(".qing_scan_input_panel").toHaveCount(0);
    expect(".qing_scan_validate").toHaveCount(0);
    expect(".qing_scan_feedback").toHaveCount(0);
    expect(".qing_scan_completed_filter").toHaveCount(0);
    expect(component.documentProgress).toBe(100);
    await contains(".qing_scan_refresh").click();
    expect(reads).toBe(2);
    await contains(".qing_scan_return").click();
    expect(component.state.session).toBe(null);
    expect(calls.at(-1)).toEqual(["outgoing", "", true]);
    expect(".qing_scan_view").toHaveCount(1);
    await component.changePage("lookup");
    await animationFrame();
    expect(".qing_scan_completed_filter").toHaveCount(0);
});


test("explicit transfer entry wins over an old saved session and selects its operation", async () => {
    patchWithCleanup(browser.localStorage, { getItem: (key) => key.endsWith(":session") ? "9" : null });
    const calls = [];
    onRpc("qing.stock.scan.session", "dashboard", ({ args }) => { calls.push(args[0]); return { company: "Test", transfers: [] }; });
    onRpc("qing.stock.scan.session", "get_session", () => { throw Error("Must not restore unrelated saved work"); });
    onRpc("qing.stock.scan.session", "start_picking", ({ args }) => {
        expect(args[0]).toBe(42);
        return { id: 10, name: "WH/OUT/42", state: "scanning", kind: "picking", operation: "outgoing", owned: true, picking_id: 42, lines: [], moves: [], events: [] };
    });
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: { picking_id: 42, operation: "outgoing", picking_state: "assigned" } } } });
    expect(calls).toEqual(["outgoing"]);
    expect(component.state.page).toBe("outgoing");
    expect(component.state.session.picking_id).toBe(42);
});

test("completed transfer entry opens readonly native details without starting work", async () => {
    patchWithCleanup(browser.localStorage, { getItem: (key) => key.endsWith(":session") ? "9" : null });
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", transfers: [] }));
    onRpc("qing.stock.scan.session", "get_session", () => { throw Error("Must not restore unrelated saved work"); });
    onRpc("qing.stock.scan.session", "start_picking", () => { throw Error("Completed picking must not start"); });
    onRpc("qing.stock.scan.session", "get_completed_picking", ({ args }) => {
        expect(args).toEqual([42]);
        return { id: false, readonly: true, name: "WH/INT/42", state: "done", kind: "picking", operation: "internal", picking_state: "done", picking_id: 42, lines: [], moves: [], events: [] };
    });
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: { picking_id: 42, operation: "internal", picking_state: "done" } } } });
    expect(component.state.page).toBe("internal");
    expect(".qing_scan_return").toHaveCount(1);
    expect(".qing_scan_input_panel").toHaveCount(0);
});

test("pending requests keep their original session ahead of an explicit transfer entry", async () => {
    const pending = [{ sessionId: 9, key: "original-request", payload: { op: "scan", code: "A" } }];
    patchWithCleanup(browser.localStorage, { getItem: (key) => key.endsWith(":requests") ? JSON.stringify(pending) : null });
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", transfers: [] }));
    onRpc("qing.stock.scan.session", "start_picking", () => { throw Error("Must resolve original pending request first"); });
    onRpc("qing.stock.scan.session", "get_session", ({ args }) => {
        expect(args).toEqual([[9]]);
        return { id: 9, name: "WH/IN/9", state: "scanning", kind: "picking", operation: "incoming", picking_id: 9, lines: [], moves: [], events: [] };
    });
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: { picking_id: 42, operation: "outgoing" } } } });
    expect(component.state.session.id).toBe(9);
    expect(component.state.page).toBe("incoming");
    expect(component.queue.items).toEqual(pending);
    expect(component.state.paused).toBe(true);
});

test("internal drafts expose creation and editing while explicit list return ignores saved work", async () => {
    const formAction = { type: "ir.actions.act_window", res_model: "stock.picking", views: [[false, "form"]] };
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", can_create_internal: true,
        transfers: [{ id: 42, name: "WH/INT/DRAFT", state: "draft", source: "A", destination: "B" }] }));
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: { operation: "internal", list_only: true } } } });
    const calls = [];
    patchWithCleanup(component.orm, { call: async (model, method, args) => { calls.push([model, method, args]); return formAction; } });
    patchWithCleanup(component.actions, { doAction: async (action) => { expect(action).toEqual(formAction); } });
    expect(".qing_scan_new").toHaveCount(1);
    expect(".qing_scan_edit").toHaveCount(1);
    expect(".qing_scan_start").toHaveCount(0);
    await contains(".qing_scan_new").click();
    await contains(".qing_scan_edit").click();
    expect(calls).toEqual([["stock.picking", "action_qing_new_internal", []], ["stock.picking", "action_qing_edit_internal", [[42]]]]);
    component.state.pending = 1;
    await animationFrame();
    await component.editInternal();
    expect(calls).toHaveLength(2);
    component.state.pending = 0;
    component.state.online = false;
    await component.editInternal();
    expect(calls).toHaveLength(2);
});

test("saving a draft returns to the internal list instead of an unrelated saved session", async () => {
    patchWithCleanup(browser.localStorage, { getItem: (key) => key.endsWith(":session") ? "99" : null });
    onRpc("qing.stock.scan.session", "dashboard", () => ({ company: "Test", transfers: [] }));
    onRpc("qing.stock.scan.session", "get_session", () => { throw new Error("Must not reopen a saved session on explicit list return"); });
    const component = await mountWithCleanup(WarehouseWorkbench, { props: { action: { params: { operation: "internal", list_only: true } } } });
    expect(component.state.page).toBe("internal");
    expect(component.state.session).toBe(null);
    expect(".qing_scan_input_panel").toHaveCount(0);
    expect(".qing_scan_new").toHaveCount(0);
});

/** @odoo-module **/

import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, mockService, mountWithCleanup } from "@web/../tests/web_test_helpers";
import { AccountLedger } from "@qing_account_reports/account_ledger";

defineMailModels();

function mockLedger() {
    mockService("orm", {
        async call(model, method, args) {
            if (method === "get_accounts") return [{ id: 1, name: "Cash" }, { id: 2, name: "Bank" }];
            if (method === "get_report") {
                const [account_id, date_from, date_to, offset = 0] = args;
                expect.step(`report:${account_id}:${offset}`);
                return { account_id, date_from, date_to, company: "Test", currency: "CNY", precision: 2,
                    account: account_id === 1 ? "Cash" : "Bank", opening: 100, closing: 130, debit: 50, credit: 20,
                    count: 81, offset, limit: 80, rows: [{ id: 1 + offset, move_id: 1, date: date_from,
                        move: "MISC/1", journal: "Misc", partner: "", label: "", debit: 50, credit: 0,
                        balance: 50, running: offset ? 130 : 150, residual: false, currency: "CNY", amount_currency: 50 }] };
            }
        },
    });
}

test("Owl 3 ledger renders, binds account selection and paginates", async () => {
    mockLedger();
    await mountWithCleanup(AccountLedger);
    expect("select").toHaveValue("1");
    expect("table tbody").toHaveText(/150/);
    await contains('button[title="Next Page"]').click();
    expect("table tbody").toHaveText(/130/);
    await contains("select").select("2");
    expect('button[title="Download CSV"]').not.toBeEnabled();
    await contains("header .btn-primary").click();
    expect("select").toHaveValue("2");
    expect.verifySteps(["report:1:0", "report:1:80", "report:2:0"]);
});

test("changing the date scope disables stale export and invalid generation", async () => {
    mockLedger();
    await mountWithCleanup(AccountLedger);
    await contains('input[type="date"]:first').edit("2099-01-01");
    expect('button[title="Download CSV"]').not.toBeEnabled();
    expect("header .btn-primary").not.toBeEnabled();
    expect.verifySteps(["report:1:0"]);
});

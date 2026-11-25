/** @odoo-module **/

import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { makeErrorFromResponse } from "@web/core/network/rpc";
import { contains, mockService, mountWithCleanup } from "@web/../tests/web_test_helpers";
import { FinancialReport } from "@qing_account_reports/financial_report";

defineMailModels();

test("financial reports show validation messages and clear them after a successful refresh", async () => {
    const reportDate = "2025-12-31";
    let failure;
    mockService("orm", {
        async call() {
            if (failure) {
                throw makeErrorFromResponse({
                    code: 200,
                    message: "Odoo Server Error",
                    data: failure,
                });
            }
            return { status: "setup", date_to: reportDate, message: "Configure a report scheme." };
        },
    });
    await mountWithCleanup(FinancialReport, { props: { action: { params: { report_type: "profit" } } } });
    const errorBanner = ".o_qing_financial_report [role='alert']";
    const refresh = ".o_qing_financial_report header button.btn-outline-primary";

    failure = { name: "odoo.exceptions.UserError", message: "The report date cannot be in the future." };
    await contains(refresh).click();
    expect(errorBanner).toHaveText("The report date cannot be in the future.");

    failure = { name: "builtins.TypeError", message: "Internal calculation details" };
    await contains(refresh).click();
    expect(errorBanner).toHaveText("Odoo Server Error");

    failure = undefined;
    await contains(refresh).click();
    expect(errorBanner).toHaveCount(0);
    expect(".o_qing_financial_report .alert-warning").toHaveText(/Configure a report scheme/);
});

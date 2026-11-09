/** @odoo-module **/
import { animationFrame, expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { defineModels, fields, models, mountView } from "@web/../tests/web_test_helpers";
import { getChart, setupChartJsForTests } from "@web/../tests/views/graph/graph_test_helpers";
import "@qing_web_ui/js/qing_graph_theme";

class QingReportRecord extends models.Model {
    _name = "qing.report.record";
    category = fields.Char();
    status = fields.Char();
    amount = fields.Float();
    _records = [
        { id: 1, category: "Services", status: "Confirmed", amount: 1200 },
        { id: 2, category: "Products", status: "Draft", amount: 800 },
    ];
}
defineModels([QingReportRecord]);
defineMailModels();
setupChartJsForTests();

for (const mode of ["bar", "line", "pie"]) {
    test(`${mode} graph follows live Qing theme and retains hidden data`, async () => {
        const preferences = window.qingWebUi.getPreferences();
        try {
            window.qingWebUi.setPreferences({ themeMode: "dark" });
            const view = await mountView({
                type: "graph", resModel: "qing.report.record",
                arch: `<graph type="${mode}"><field name="category"/><field name="status"/><field name="amount" type="measure"/></graph>`,
            });
            const chart = getChart(view);
            const data = chart.data;
            const values = JSON.stringify(data.datasets.map((dataset) => dataset.data));
            expect(chart.legend.legendItems[0].fontColor).toBe("#edf3fa");
            if (mode === "pie") {
                chart.toggleDataVisibility(0);
                expect(chart.data.datasets[0].borderColor).toBe("#1d2835");
            } else {
                chart.setDatasetVisibility(0, false);
                expect(chart.options.scales.y.ticks.color).toBe("#edf3fa");
                expect(chart.options.scales.y.grid.color).toBe("#415369");
            }
            window.qingWebUi.setPreferences({ themeMode: "light" });
            await animationFrame();
            expect(getChart(view)).toBe(chart);
            expect(chart.data).toBe(data);
            expect(JSON.stringify(data.datasets.map((dataset) => dataset.data))).toBe(values);
            expect(chart.legend.legendItems[0].fontColor).toBe("#1f2928");
            if (mode === "pie") {
                expect(chart.getDataVisibility(0)).toBe(false);
                expect(chart.data.datasets[0].borderColor).toBe("#ffffff");
            } else {
                expect(chart.isDatasetVisible(0)).toBe(false);
                expect(chart.options.scales.y.ticks.color).toBe("#1f2928");
            }
            window.qingWebUi.setPreferences({ themeMode: "dark" });
            await animationFrame();
            expect(chart.legend.legendItems[0].fontColor).toBe("#edf3fa");
        } finally {
            window.qingWebUi.setPreferences(preferences);
        }
    });
}

// Other backend themes retain Odoo's own graph rendering configuration.
test("native graph stays unchanged outside the Qing shell", async () => {
    const preferences = window.qingWebUi.getPreferences();
    try {
        window.qingWebUi.setPreferences({ themeMode: "light" });
        document.body.classList.remove("o_qing_web_ui");
        const view = await mountView({
            type: "graph", resModel: "qing.report.record",
            arch: `<graph type="line"><field name="category"/><field name="amount" type="measure"/></graph>`,
        });
        const chart = getChart(view);
        expect(chart.config.plugins.map((plugin) => plugin.id)).toEqual(["gridOnTop"]);
        expect(chart.options.scales.y.grid.display).toBe(false);
    } finally {
        document.body.classList.add("o_qing_web_ui");
        window.qingWebUi.setPreferences(preferences);
    }
});

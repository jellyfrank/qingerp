/** @odoo-module **/
import { useBus } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { GraphRenderer } from "@web/views/graph/graph_renderer";

function graphPalette() {
    if (!document.body.classList.contains("o_qing_web_ui")) {
        return null;
    }
    const style = getComputedStyle(document.body);
    return {
        text: style.getPropertyValue("--o-qing-text").trim(),
        grid: style.getPropertyValue("--o-qing-border").trim(),
        surface: style.getPropertyValue("--o-qing-surface").trim(),
    };
}

function colorChart(chartConfig, palette) {
    const { options, data, type } = chartConfig;
    for (const scale of Object.values(options.scales || {})) {
        scale.ticks = { ...scale.ticks, color: palette.text };
        scale.title = { ...scale.title, color: palette.text };
    }
    if (options.scales?.y) {
        options.scales.y.grid.color = palette.grid;
    }
    if (type === "line") {
        // Let Chart.js draw grids above the line fill, using its native scales.
        Object.assign(options.scales.y.grid, { display: true, z: 1 });
        Object.assign(options.scales.x.grid, {
            color: palette.grid, drawOnChartArea: false, z: 1,
        });
    }
    options.plugins.legend.labels.color = palette.text;
    if (type === "pie") {
        for (const dataset of data.datasets) {
            dataset.borderColor = palette.surface;
            dataset.hoverBorderColor = palette.surface;
        }
    }
}

// Preserve the native line hover guide without its cookie-based fixed color.
const qingHoverGrid = {
    id: "qingGraphHoverGrid",
    afterDraw(chart) {
        const palette = graphPalette();
        if (!palette) return;
        const { ctx, chartArea, scales } = chart;
        ctx.save();
        ctx.lineWidth = 1;
        ctx.strokeStyle = palette.grid;
        for (const [index, point] of (chart.getDatasetMeta(0).data || []).entries()) {
            if (point.active) {
                const x = scales.x.getPixelForTick(index);
                ctx.beginPath();
                ctx.moveTo(x, chartArea.top);
                ctx.lineTo(x, chartArea.bottom);
                ctx.stroke();
            }
        }
        ctx.restore();
    },
};

patch(GraphRenderer.prototype, {
    setup() {
        super.setup();
        useBus(window, "qing-web-ui:preferences-changed", () => {
            const chart = this.chart.instance();
            const palette = graphPalette();
            if (chart && palette) {
                // Update the existing chart: keep hidden series and drill-down data.
                colorChart(chart.config, palette);
                chart.update("none");
            }
        });
    },
    getLegendOptions() {
        const options = super.getLegendOptions();
        const generateLabels = options.labels.generateLabels;
        options.labels.generateLabels = (chart) => {
            const labels = generateLabels(chart);
            const palette = graphPalette();
            return palette
                ? labels.map((label) => ({ ...label, fontColor: palette.text }))
                : labels;
        };
        return options;
    },
    getChartConfig() {
        const config = super.getChartConfig();
        const palette = graphPalette();
        if (palette) {
            colorChart(config, palette);
            config.plugins = config.plugins?.map((plugin) =>
                plugin.id === "gridOnTop" ? qingHoverGrid : plugin
            );
        }
        return config;
    },
});

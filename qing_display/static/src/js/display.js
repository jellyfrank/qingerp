/** @odoo-module **/

import { Component, onMounted, onPatched, onWillStart, onWillUnmount, proxy, signal, useProps } from "@odoo/owl";
import { loadJS } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";

const REFRESH_MS = 15000;
const ANNOUNCEMENT_MS = 5000;

export class QingDisplay extends Component {
    static template = "qing_display.Screen";
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.rootRef = signal.ref();
        this.trendRef = signal.ref();
        this.shareRef = signal.ref();
        this.companyId = user.activeCompany?.id;
        this.cursor = null;
        this.charts = {};
        this.chartVersion = -1;
        this.queue = [];
        this.fetching = false;
        this.audioContext = null;
        this.state = proxy({
            announcement: null,
            clock: Date.now(),
            data: null,
            error: "",
            fullScreen: false,
            loading: true,
            soundEnabled: false,
            version: 0,
        });

        onWillStart(async () => {
            await Promise.all([
                loadJS("/qing_display/static/lib/echarts/echarts.min.js"),
                this.refresh(),
            ]);
        });
        onMounted(() => {
            this.renderCharts();
            this.refreshTimer = window.setInterval(() => this.refresh(), REFRESH_MS);
            this.clockTimer = window.setInterval(() => { this.state.clock = Date.now(); }, 60000);
            this.resizeHandler = () => Object.values(this.charts).forEach((chart) => chart.resize());
            this.fullScreenHandler = () => {
                this.state.fullScreen = Boolean(document.fullscreenElement);
                this.resizeHandler();
            };
            window.addEventListener("resize", this.resizeHandler);
            document.addEventListener("fullscreenchange", this.fullScreenHandler);
        });
        onPatched(() => {
            if (this.chartVersion !== this.state.version) {
                this.renderCharts();
            }
        });
        onWillUnmount(() => {
            window.clearInterval(this.refreshTimer);
            window.clearInterval(this.clockTimer);
            window.clearTimeout(this.announcementTimer);
            window.removeEventListener("resize", this.resizeHandler);
            document.removeEventListener("fullscreenchange", this.fullScreenHandler);
            Object.values(this.charts).forEach((chart) => chart.dispose());
            if (this.audioContext) {
                this.audioContext.close();
            }
        });
    }

    async refresh() {
        if (this.fetching || !this.companyId) {
            return;
        }
        this.fetching = true;
        try {
            const data = await this.orm.call(
                "res.company", "qing_display_snapshot", [this.companyId, this.cursor]
            );
            this.cursor = data.cursor;
            this.state.data = data;
            this.state.version += 1;
            this.state.error = "";
            this.queue.push(...data.events);
            this.showNextAnnouncement();
        } catch (error) {
            this.state.error = error.data?.message || _t("Connection lost. Retrying…");
        } finally {
            this.state.loading = false;
            this.fetching = false;
        }
    }

    async changeCompany(event) {
        this.companyId = Number(event.target.value);
        this.cursor = null;
        this.queue = [];
        this.state.announcement = null;
        window.clearTimeout(this.announcementTimer);
        await this.refresh();
    }

    showNextAnnouncement() {
        if (this.state.announcement || !this.queue.length) {
            return;
        }
        this.state.announcement = this.queue.shift();
        if (this.state.soundEnabled) {
            this.playTone();
        }
        this.announcementTimer = window.setTimeout(() => {
            this.state.announcement = null;
            this.showNextAnnouncement();
        }, ANNOUNCEMENT_MS);
    }

    async toggleSound() {
        if (this.state.soundEnabled) {
            this.state.soundEnabled = false;
            return;
        }
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            this.audioContext ||= new AudioContext();
            await this.audioContext.resume();
            this.state.soundEnabled = this.audioContext.state === "running";
            if (this.state.soundEnabled) {
                this.playTone();
            }
        } catch {
            this.state.error = _t("Sound is unavailable on this device.");
        }
    }

    playTone() {
        if (!this.audioContext || this.audioContext.state !== "running") {
            this.state.soundEnabled = false;
            return;
        }
        const now = this.audioContext.currentTime;
        for (const [index, frequency] of [660, 880, 1046].entries()) {
            const oscillator = this.audioContext.createOscillator();
            const volume = this.audioContext.createGain();
            oscillator.type = "sine";
            oscillator.frequency.value = frequency;
            volume.gain.setValueAtTime(0.0001, now + index * 0.16);
            volume.gain.exponentialRampToValueAtTime(0.09, now + index * 0.16 + 0.02);
            volume.gain.exponentialRampToValueAtTime(0.0001, now + index * 0.16 + 0.25);
            oscillator.connect(volume).connect(this.audioContext.destination);
            oscillator.start(now + index * 0.16);
            oscillator.stop(now + index * 0.16 + 0.26);
        }
    }

    async toggleFullScreen() {
        if (document.fullscreenElement) {
            await document.exitFullscreen();
        } else {
            await this.rootRef()?.requestFullscreen();
        }
    }

    leave() {
        window.location.assign("/web");
    }

    formatNumber(value) {
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value || 0);
    }

    formatMoney(value) {
        return `${this.state.data?.currency || ""}${this.formatNumber(value)}`;
    }

    get clockText() {
        return new Intl.DateTimeFormat(undefined, {
            year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit", hour12: false,
        }).format(new Date(this.state.clock));
    }

    get dailyShare() {
        const data = this.state.data;
        if (!data?.month_amount) {
            return 0;
        }
        return Math.min(100, Math.max(0, Math.round(data.today_amount / data.month_amount * 100)));
    }

    renderCharts() {
        if (!window.echarts || !this.state.data) {
            return;
        }
        const data = this.state.data;
        const trendNode = this.trendRef();
        const shareNode = this.shareRef();
        if (!trendNode || !shareNode) {
            return;
        }
        this.charts.trend ||= window.echarts.init(trendNode);
        this.charts.share ||= window.echarts.init(shareNode);
        this.charts.trend.setOption({
            animationDuration: 450,
            grid: { left: 48, right: 20, top: 24, bottom: 36 },
            tooltip: { trigger: "axis", valueFormatter: (value) => this.formatMoney(value) },
            xAxis: {
                type: "category", data: data.trends.map((point) => point.label),
                axisLine: { lineStyle: { color: "#31668d" } },
                axisLabel: { color: "#a6cfe4", fontSize: 12 }, axisTick: { show: false },
            },
            yAxis: {
                type: "value", splitLine: { lineStyle: { color: "#174365" } },
                axisLabel: { color: "#81b8d5", formatter: (value) => this.formatNumber(value) },
            },
            series: [{
                type: "line", smooth: true, symbolSize: 8,
                data: data.trends.map((point) => point.amount),
                lineStyle: { width: 3, color: "#26dbef" },
                itemStyle: { color: "#52f0f7" },
                areaStyle: { color: "rgba(20, 181, 223, .25)" },
            }],
        });
        this.charts.share.setOption({
            animationDuration: 450,
            series: [{
                type: "pie", radius: ["68%", "83%"], center: ["50%", "48%"],
                silent: true, label: { show: false },
                data: [
                    { value: Math.max(0, data.today_amount), itemStyle: { color: "#25d9ec" } },
                    { value: Math.max(0, data.month_amount - data.today_amount),
                      itemStyle: { color: "#194466" } },
                ],
            }],
        });
        this.chartVersion = this.state.version;
        this.resizeHandler?.();
    }
}

registry.category("actions").add("qing_display.screen", QingDisplay);

import { Component, onWillStart, onMounted, onPatched, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";

const PALETTE = {
    fba: "#2563eb",
    fbm: "#f59e0b",
    margin: "#10b981",
    donut: ["#2563eb", "#f59e0b", "#10b981", "#8b5cf6", "#ef4444", "#06b6d4", "#64748b"],
};

const HEALTH = [
    { key: "ok", label: _t("In sync"), cls: "ok" },
    { key: "under", label: _t("Under-listed"), cls: "warn" },
    { key: "oversell", label: _t("Oversell risk"), cls: "bad" },
    { key: "unmapped", label: _t("No product"), cls: "bad" },
    { key: "fba", label: _t("Amazon (FBA)"), cls: "info" },
    { key: "off", label: _t("Not synced"), cls: "muted" },
];

export class AmazonDashboard extends Component {
    static template = "chi_amazon_connector.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.salesChartRef = useRef("salesChart");
        this.donutRef = useRef("donut");
        this.charts = [];
        this.health = HEALTH;
        this.state = useState({
            loading: true,
            syncing: false,
            accountId: false,
            days: 30,
            fulfillment: "all",
            data: null,
        });
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        this.version = 0;
        this.renderedVersion = -1;
        const maybeRender = () => {
            if (this.renderedVersion !== this.version) {
                this.renderedVersion = this.version;
                this.renderCharts();
            }
        };
        onMounted(maybeRender);
        onPatched(maybeRender);
        onWillUnmount(() => this.destroyCharts());
    }

    // ------------------------------------------------------------------ data
    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("chi.amazon.dashboard", "get_data", [], {
            account_id: this.state.accountId,
            days: this.state.days,
            fulfillment: this.state.fulfillment,
        });
        this.version++;
        this.state.loading = false;
    }

    async refresh() {
        await this.load();
    }

    async setAccount(id) {
        this.state.accountId = id;
        await this.refresh();
    }
    async setDays(days) {
        this.state.days = days;
        await this.refresh();
    }
    async setFulfillment(value) {
        this.state.fulfillment = value;
        await this.refresh();
    }

    async syncNow() {
        this.state.syncing = true;
        try {
            const ids = this.state.accountId
                ? [this.state.accountId]
                : this.state.data.accounts.filter((a) => a.state !== "draft").map((a) => a.id);
            if (!ids.length) {
                this.notification.add(_t("Connect an Amazon account first."), { type: "warning" });
                return;
            }
            await this.orm.call("chi.amazon.account", "action_sync_orders", [ids]);
            this.notification.add(_t("Orders synced from Amazon"), { type: "success" });
            await this.refresh();
        } finally {
            this.state.syncing = false;
        }
    }

    // ------------------------------------------------------------ navigation
    async openAttention(key) {
        const action = await this.orm.call("chi.amazon.dashboard", "get_attention_action", [key], {
            account_id: this.state.accountId,
        });
        this.action.doAction(action);
    }
    openModel(model, name, domain = [], views = [[false, "list"], [false, "form"]]) {
        const base = this.state.accountId ? [["account_id", "=", this.state.accountId]] : [];
        this.action.doAction({
            type: "ir.actions.act_window", res_model: model, name, views,
            domain: [...base, ...domain],
        });
    }
    openRecord(model, id) {
        if (id) {
            this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id,
                views: [[false, "form"]] });
        }
    }
    openKpi(key) {
        if (["sales", "orders", "aov", "margin"].includes(key)) {
            this.openModel("chi.amazon.order", _t("Amazon Orders"), [["amazon_status", "!=", "canceled"]]);
        } else if (key === "fees") {
            this.openModel("chi.amazon.settlement", _t("Payouts"));
        } else if (key === "refunds") {
            this.openModel("chi.amazon.return", _t("Returns"), [], [[false, "kanban"], [false, "list"], [false, "form"]]);
        }
    }
    openHealth(key) {
        this.openModel("chi.amazon.listing", _t("Listings"), [["stock_health", "=", key]]);
    }
    openSetup() {
        this.action.doAction("chi_amazon_connector.action_amazon_account");
    }

    // ------------------------------------------------------------ formatting
    money(value, compact = false) {
        const cur = this.state.data?.currency || {};
        const abs = Math.abs(value || 0);
        const sign = value < 0 ? "-" : "";
        let num;
        if (compact && abs >= 1000) {
            num = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(abs);
        } else {
            num = new Intl.NumberFormat(undefined, {
                minimumFractionDigits: abs >= 1000 ? 0 : 2,
                maximumFractionDigits: abs >= 1000 ? 0 : 2,
            }).format(abs);
        }
        return cur.position === "after" ? `${sign}${num} ${cur.symbol}` : `${sign}${cur.symbol}${num}`;
    }
    number(value) {
        return new Intl.NumberFormat().format(Math.round(value || 0));
    }
    kpiValue(kpi) {
        return kpi.money ? this.money(kpi.value) : this.number(kpi.value);
    }
    deltaClass(kpi) {
        if (!kpi.delta) {
            return "flat";
        }
        const good = kpi.inverse ? kpi.delta < 0 : kpi.delta > 0;
        return good ? "up" : "down";
    }
    deltaLabel(kpi) {
        if (!kpi.delta) {
            return "—";
        }
        return `${kpi.delta > 0 ? "▲" : "▼"} ${Math.abs(kpi.delta).toFixed(0)}%`;
    }
    sparkPath(values, w = 120, h = 34) {
        if (!values || values.length < 2) {
            return "";
        }
        const max = Math.max(...values, 1);
        const min = Math.min(...values, 0);
        const step = w / (values.length - 1);
        const pts = values.map((v, i) => [i * step, h - ((v - min) / (max - min || 1)) * (h - 4) - 2]);
        return pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
    }
    sparkArea(values, w = 120, h = 34) {
        const line = this.sparkPath(values, w, h);
        return line ? `${line} L${w},${h} L0,${h} Z` : "";
    }
    healthPct(key) {
        const total = this.state.data?.listing_total || 0;
        return total ? ((this.state.data.health[key] || 0) / total) * 100 : 0;
    }
    get periodLabel() {
        return { 7: _t("Last 7 days"), 30: _t("Last 30 days"), 90: _t("Last 90 days") }[this.state.days];
    }
    get greeting() {
        const h = new Date().getHours();
        return h < 12 ? _t("Good morning") : h < 18 ? _t("Good afternoon") : _t("Good evening");
    }
    get mkTotal() {
        return (this.state.data?.marketplaces || []).reduce((s, m) => s + m.value, 0);
    }
    mkColor(index) {
        return PALETTE.donut[index % PALETTE.donut.length];
    }
    shortDate(value) {
        if (!value) {
            return "";
        }
        const d = new Date(value.replace(" ", "T") + (value.length > 10 ? "Z" : ""));
        return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }
    timeAgo(value) {
        if (!value) {
            return _t("never");
        }
        const diff = (Date.now() - new Date(value.replace(" ", "T") + "Z").getTime()) / 60000;
        if (diff < 1) {
            return _t("just now");
        }
        if (diff < 60) {
            return `${Math.round(diff)} min ago`;
        }
        if (diff < 1440) {
            return `${Math.round(diff / 60)} h ago`;
        }
        return `${Math.round(diff / 1440)} d ago`;
    }

    // ---------------------------------------------------------------- charts
    destroyCharts() {
        this.charts.forEach((c) => c.destroy());
        this.charts = [];
    }

    renderCharts() {
        this.destroyCharts();
        const data = this.state.data;
        if (!data || !data.accounts.length || !window.Chart) {
            return;
        }
        const root = this.salesChartRef.el ? getComputedStyle(this.salesChartRef.el) : null;
        const textColor = (root && root.getPropertyValue("--amz-muted").trim()) || "#6b7280";
        const gridColor = (root && root.getPropertyValue("--amz-line").trim()) || "#e5e7eb";
        const labels = data.series.labels.map((l) => this.shortDate(l));

        if (this.salesChartRef.el) {
            const ctx = this.salesChartRef.el.getContext("2d");
            const grad = ctx.createLinearGradient(0, 0, 0, 260);
            grad.addColorStop(0, "rgba(16,185,129,0.25)");
            grad.addColorStop(1, "rgba(16,185,129,0)");
            const datasets = [];
            if (data.fulfillment !== "fbm") {
                datasets.push({ type: "bar", label: "FBA", data: data.series.fba, backgroundColor: PALETTE.fba,
                    borderRadius: 4, stack: "s", maxBarThickness: 22, order: 2 });
            }
            if (data.fulfillment !== "fba") {
                datasets.push({ type: "bar", label: "FBM", data: data.series.fbm, backgroundColor: PALETTE.fbm,
                    borderRadius: 4, stack: "s", maxBarThickness: 22, order: 2 });
            }
            datasets.push({ type: "line", label: _t("Margin"), data: data.series.margin, borderColor: PALETTE.margin,
                backgroundColor: grad, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 4,
                borderWidth: 2, order: 1 });
            this.charts.push(new window.Chart(ctx, {
                data: { labels, datasets },
                options: {
                    responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
                    interaction: { mode: "index", intersect: false },
                    plugins: {
                        legend: { display: true, position: "top", align: "end",
                            labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, color: textColor } },
                        tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${this.money(c.parsed.y)}` } },
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false }, ticks: { color: textColor, maxTicksLimit: 10 } },
                        y: { stacked: true, beginAtZero: true, grid: { color: gridColor },
                            border: { display: false },
                            ticks: { color: textColor, callback: (v) => this.money(v, true) } },
                    },
                },
            }));
        }
        if (this.donutRef.el && data.marketplaces.length) {
            this.charts.push(new window.Chart(this.donutRef.el, {
                type: "doughnut",
                data: {
                    labels: data.marketplaces.map((m) => m.name),
                    datasets: [{ data: data.marketplaces.map((m) => m.value),
                        backgroundColor: data.marketplaces.map((m, i) => this.mkColor(i)), borderWidth: 0 }],
                },
                options: {
                    cutout: "72%", responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false },
                        tooltip: { callbacks: { label: (c) => ` ${c.label}: ${this.money(c.parsed)}` } } },
                },
            }));
        }
    }
}

registry.category("actions").add("chi_amazon_dashboard", AmazonDashboard);

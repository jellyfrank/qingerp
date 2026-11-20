/** @odoo-module **/

import { Component, onWillDestroy, onWillStart, proxy } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const MODEL = "qing.ai.guide";

export class QingAiGuide extends Component {
    static template = "qing_ai.Guide";

    setup() {
        this.orm = useService("orm");
        this.bus = useService("bus_service");
        this.requestKey = null;
        const receive = ({ request_key: requestKey, text }) => {
            if (requestKey === this.requestKey) {
                this.state.partial = text;
            }
        };
        this.bus.subscribe("qing_ai/guide_stream", receive);
        onWillDestroy(() => this.bus.unsubscribe("qing_ai/guide_stream", receive));
        this.state = proxy({
            config: null,
            filter: "",
            provider: "default",
            topic: "",
            question: "",
            partial: "",
            answer: null,
            source: null,
            busy: false,
            error: "",
        });
        onWillStart(async () => {
            try {
                this.state.config = await this.orm.call(MODEL, "guide_configuration");
            } catch (error) {
                this.showError(error);
            }
        });
    }

    showError(error) {
        this.state.error = error.data?.message || _t("The guide request failed. Please try again.");
    }

    get topics() {
        const query = this.state.filter.trim().toLowerCase();
        const items = this.state.config?.topics || [];
        if (!query) {
            return items;
        }
        return items.filter(({ title, keywords, questions }) =>
            title.toLowerCase().includes(query) ||
            questions.some(({ text }) => text.toLowerCase().includes(query)) ||
            keywords.some((keyword) => {
                const term = keyword.toLowerCase();
                return term.includes(query) || query.includes(term);
            })
        );
    }

    get questions() {
        return this.state.config?.topics.find(({ topic }) => topic === this.state.topic)?.questions || [];
    }

    setTopic(event) {
        this.state.topic = event.target.value;
        this.state.question = "";
    }

    filterTopics(event) {
        this.state.filter = event.target.value;
        this.state.topic = "";
        this.state.question = "";
    }

    async changeTitleConsent(event) {
        const enabled = event.target.checked;
        const priorConsent = this.state.config.title_consent;
        this.state.config.title_consent = enabled;
        this.state.error = "";
        this.state.busy = true;
        try {
            this.state.config.title_consent = await this.orm.call(MODEL, "set_guide_title_consent", [enabled]);
            if (!enabled && this.state.answer) {
                this.state.answer.titles = [];
            }
        } catch (error) {
            this.state.config.title_consent = priorConsent;
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async answer() {
        if (!this.state.topic || this.state.busy) {
            return;
        }
        this.state.error = "";
        this.state.answer = null;
        this.state.source = null;
        this.state.busy = true;
        this.requestKey = crypto.randomUUID().replaceAll("-", "");
        try {
            this.state.answer = await this.orm.call(
                MODEL, "guide_answer", [this.state.topic, this.state.provider, this.requestKey, this.state.question || null]
            );
        } catch (error) {
            this.showError(error);
        } finally {
            this.requestKey = null;
            this.state.partial = "";
            this.state.busy = false;
        }
    }

    async openSource(source) {
        this.state.error = "";
        this.state.source = null;
        this.state.busy = true;
        try {
            this.state.source = await this.orm.call(
                MODEL, "guide_citation", [this.state.answer.topic, source.uid]
            );
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("qing_ai.guide", QingAiGuide);

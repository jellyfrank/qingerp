<script setup>
import { computed, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { Button as VanButton, PullRefresh as VanPullRefresh } from "vant";
import { absolute, embeddedSettings, nativeDevice, request, sameOrigin } from "./api.js";
import { languages, messages } from "./messages.js";
import { version } from "../package.json";

const originKey = "qing-b2b:" + absolute("/");
const authKey = originKey + ":device";
const read = (store, key, fallback) => {
  try { return JSON.parse(store.getItem(key)) ?? fallback; } catch { return fallback; }
};
const device = ref(read(sessionStorage, authKey, null) || read(localStorage, authKey, null));
if (device.value && Date.parse(device.value.expires_at.replace(" ", "T") + "Z") <= Date.now()) {
  device.value = null;
  localStorage.removeItem(authKey);
  sessionStorage.removeItem(authKey);
}

const settings = ref(null);
const lang = ref(localStorage.getItem(originKey + ":language") || "en");
const text = (key) => messages[lang.value]?.[key] ?? messages.en[key] ?? key;
const compatible = computed(() => settings.value?.version.split(".")[0] === "1");
const hasCatalog = computed(() => settings.value?.capabilities.includes("catalog"));
const presets = ["fresh", "lookbook", "gold"];
const preset = computed(() => presets.includes(settings.value?.theme) ? settings.value.theme : "");
const shell = ref(null);
const quick = ref(null);
const searchOpen = ref(false);
const theme = computed(() => {
  const color = settings.value?.color;
  if (!/^#[0-9a-f]{6}$/i.test(color || "")) return {};
  const [red, green, blue] = [1, 3, 5].map((start) => {
    const channel = parseInt(color.slice(start, start + 2), 16) / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  // Light accents take dark lettering so buttons stay readable.
  const light = 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.4;
  return { "--brand": color, "--on-brand": light ? "#14202e" : "#fff" };
});
const glyphs = {
  catalog: "M3.5 7.8 12 3.5l8.5 4.3v8.4L12 20.5l-8.5-4.3V7.8ZM3.5 7.8 12 12l8.5-4.2M12 12v8.5",
  cart: "M3 4.5h2.3l2.2 10h9.6l1.9-7.2H6.6M8.2 18.7a1.1 1.1 0 1 0 2.2 0 1.1 1.1 0 1 0-2.2 0ZM15.4 18.7a1.1 1.1 0 1 0 2.2 0 1.1 1.1 0 1 0-2.2 0Z",
  history: "M6.5 3.5h11v17l-2.75-1.8L12 20.5l-2.75-1.8L6.5 20.5v-17ZM9.5 8.5h5M9.5 12h5",
  account: "M12 12a3.6 3.6 0 1 0 0-7.2 3.6 3.6 0 0 0 0 7.2ZM5 20c.7-3.4 3.4-5.3 7-5.3s6.3 1.9 7 5.3",
  search: "M10.8 17.3a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM15.6 15.6 20 20",
  back: "M14.5 5.5 8 12l6.5 6.5",
  remove: "m6.5 6.5 11 11M17.5 6.5l-11 11",
  plus: "M12 5v14M5 12h14",
  phone: "M6.6 3.5h2.9l1.5 4-2 1.5a11 11 0 0 0 5 5l1.5-2 4 1.5v2.9a2 2 0 0 1-2.2 2A16 16 0 0 1 4.6 5.7a2 2 0 0 1 2-2.2Z",
  mail: "M3.5 6.5h17v11h-17v-11ZM3.5 7l8.5 6 8.5-6",
};
const Glyph = (props) => h("svg", {
  viewBox: "0 0 24 24", width: props.size || 22, height: props.size || 22, fill: "none", stroke: "currentColor",
  "stroke-width": 1.7, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
}, [h("path", { d: glyphs[props.name] })]);
Glyph.props = ["name", "size"];
const logo = computed(() => settings.value?.logo ? absolute(settings.value.logo) : "");
const page = ref("catalog");
const status = ref("");
const errors = ref([]);
const failed = ref(false);
let statusTimer;
const busy = ref(false);
const login = reactive({ login: "", password: "", remember: false, totp: "" });
const askCode = ref(false);
const nativeLogin = ref(false);
const password = reactive({ current_password: "", new_password: "" });
const products = ref([]);
const currentProduct = ref(null);
const categories = ref([]);
const query = ref("");
const category = ref("");
const moreProducts = ref(false);
const picks = reactive({});
const imageUrls = reactive({});
const productGrid = ref(null);
const loadingImages = new Set();
let imageObserver;
let imageRequest = new AbortController();
let catalogRequest;
const cartKey = computed(() => originKey + ":cart:" + device.value?.account);
const cart = ref([]);
const orderQuery = ref("");
const orders = ref([]);
const moreOrders = ref(false);
const refreshing = ref(false);
const profile = ref(null);
const deviceUntil = computed(() => {
  const expires = device.value && new Date(device.value.expires_at.replace(" ", "T") + "Z");
  return expires && !Number.isNaN(expires.getTime()) ? expires.toLocaleDateString(lang.value, { year: "numeric", month: "long", day: "numeric" }) : "";
});
let ordersTimer;
const activeOrder = ref(null);
const addresses = ref([]);
const address = ref(null);
const note = ref("");
const preview = ref(null);
const pending = ref(null);
const confirmed = ref(null);
const acceptBackorder = ref(false);
const showAddress = ref(false);
const countries = ref([]);
const states = ref([]);
const addressForm = reactive({ name: "", street: "", city: "", zip: "", country: "", state: "" });
const selectedCountry = computed(() => countries.value.find((row) => row.code === addressForm.country));
const count = computed(() => cart.value.reduce((sum, line) => sum + Number(line.quantity || 0), 0));
const inCart = computed(() => {
  const totals = {};
  for (const line of cart.value) totals[line.product] = (totals[line.product] || 0) + Number(line.quantity || 0);
  return totals;
});
// The page background and the browser toolbar follow the preset and the merchant accent.
watch([preset, theme, () => !device.value], async () => {
  document.documentElement.dataset.theme = preset.value;
  await nextTick();
  const color = shell.value && getComputedStyle(shell.value).getPropertyValue("--mast").trim();
  if (color) document.querySelector('meta[name="theme-color"]')?.setAttribute("content", color);
});

function clearStatus() { status.value = ""; errors.value = []; failed.value = false; }
// Confirmations clear themselves; failures stay until the next action.
watch(status, (value) => {
  clearTimeout(statusTimer);
  if (!value || failed.value || errors.value.length) return;
  statusTimer = setTimeout(() => { if (status.value === value) status.value = ""; }, 2600);
});
function unitOf(item) { return item.units.find((row) => row.id === picks[item.id]?.unit); }
function step(target, change) {
  target.quantity = Math.min(100000, Math.max(1, (Math.trunc(Number(target.quantity)) || 1) + change));
}
function report(error) {
  failed.value = true;
  errors.value = error.details || [];
  status.value = error.status ? error.message || text("unavailable") : text("offline");
}
function money(amount, currency) {
  return new Intl.NumberFormat(lang.value, { style: "currency", currency }).format(amount);
}
function stateName(state) { return text(state === "cancel" ? "cancelState" : state); }
function deliveryName(delivery) {
  return text(delivery === "full" ? "shipped" : delivery === "pending" ? "pendingShipment" : "partShipped");
}
function changeLanguage() {
  localStorage.setItem(originKey + ":language", lang.value);
  document.documentElement.lang = lang.value;
}
function discardImages() {
  imageObserver?.disconnect();
  imageRequest.abort();
  imageRequest = new AbortController();
  loadingImages.clear();
  for (const id in imageUrls) { URL.revokeObjectURL(imageUrls[id]); delete imageUrls[id]; }
}
async function loadImage(id) {
  if (!device.value || imageUrls[id] || loadingImages.has(id)) return;
  const signal = imageRequest.signal;
  loadingImages.add(id);
  try {
    const blob = await api("image/" + id, { signal, binary: true });
    if (!signal.aborted) imageUrls[id] = URL.createObjectURL(blob);
  } catch {
    // An unavailable image leaves the existing placeholder visible.
  } finally {
    if (!signal.aborted) loadingImages.delete(id);
  }
}
function observeProductImages(grid = productGrid.value) {
  imageObserver?.disconnect();
  if (!grid) return;
  const photos = grid.querySelectorAll("[data-product-image]");
  if (!("IntersectionObserver" in window)) {
    for (const photo of photos) loadImage(photo.dataset.productImage);
    return;
  }
  imageObserver ??= new IntersectionObserver((entries, observer) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      loadImage(entry.target.dataset.productImage);
    }
  }, { rootMargin: "80px 0px" });
  for (const photo of photos) {
    if (!imageUrls[photo.dataset.productImage]) imageObserver.observe(photo);
  }
}
watch(productGrid, observeProductImages, { flush: "post" });
onBeforeUnmount(() => {
  clearTimeout(statusTimer);
  clearInterval(ordersTimer);
  document.removeEventListener("visibilitychange", refreshVisibleOrders);
  catalogRequest?.abort();
  discardImages();
});
function forget() {
  const key = cartKey.value;
  device.value = null;
  localStorage.removeItem(authKey);
  sessionStorage.removeItem(authKey);
  localStorage.removeItem(key);
  localStorage.removeItem(key + ":submission");
  cart.value = [];
  pending.value = null;
  addresses.value = [];
  orders.value = [];
  products.value = [];
  profile.value = null;
  discardImages();
}
async function api(path, options = {}) {
  try {
    return await request(path, { token: device.value?.token, ...options });
  } catch (error) {
    if (error.status === 401 && device.value) forget();
    throw error;
  }
}
async function action(callback) {
  clearStatus();
  busy.value = true;
  try { return await callback(); }
  catch (error) { report(error); }
  finally { busy.value = false; }
}
async function enter(payload, account, remember) {
  device.value = { ...payload, account };
  const store = remember ? localStorage : sessionStorage;
  store.setItem(authKey, JSON.stringify(device.value));
  const restoredPending = read(localStorage, cartKey.value + ":submission", null);
  cart.value = read(localStorage, cartKey.value, []);
  pending.value = restoredPending;
  if (pending.value) { page.value = "cart"; return; }
  await openPage("catalog");
}
async function signIn() {
  await action(async () => {
    try {
      const { totp, ...credentials } = login;
      const result = await request("login", { data: { ...credentials, ...(totp ? { totp } : {}) } });
      await enter(result, login.login.trim().toLowerCase(), login.remember);
      login.password = "";
      login.totp = "";
      askCode.value = false;
    } catch (error) {
      askCode.value = error.code === "MFA_REQUIRED" || askCode.value;
      nativeLogin.value = error.code === "NATIVE_LOGIN_REQUIRED";
      throw error;
    }
  });
}
async function signOut() {
  await action(async () => { await api("logout", { method: "POST" }); forget(); });
}
async function changePassword() {
  await action(async () => { await api("password", { data: password }); forget(); });
}
function clearPreview() {
  preview.value = null;
  pending.value = null;
  acceptBackorder.value = false;
}
watch([cart, address, note], () => {
  if (!device.value) return;
  clearPreview();
  localStorage.setItem(cartKey.value, JSON.stringify(cart.value));
}, { deep: true, flush: "sync" });
watch(pending, () => {
  if (!device.value) return;
  if (pending.value) localStorage.setItem(cartKey.value + ":submission", JSON.stringify(pending.value));
  else localStorage.removeItem(cartKey.value + ":submission");
}, { flush: "sync" });

async function loadCatalog(more = false) {
  catalogRequest?.abort();
  catalogRequest = new AbortController();
  const signal = catalogRequest.signal;
  const params = new URLSearchParams({ offset: more ? products.value.length : 0 });
  if (query.value.trim()) params.set("search", query.value.trim());
  if (category.value) params.set("category", category.value);
  try {
    const result = await api("catalog?" + params, { signal });
    if (!more) discardImages();
    products.value = more ? [...products.value, ...result.items] : result.items;
    moreProducts.value = result.has_more;
    for (const item of result.items) {
      picks[item.id] = { unit: item.units[0]?.id, quantity: 1 };
      item.currency = result.currency;
    }
    await nextTick();
    if (!signal.aborted) observeProductImages();
  } catch (error) {
    if (error.name !== "AbortError") throw error;
  }
}
function openQuick(item) {
  clearStatus();
  quick.value = item;
  nextTick(() => document.querySelector(".quick-sheet")?.focus());
}
function addQuick() {
  add(quick.value);
  quick.value = null;
}
function pickCategory(id) {
  category.value = id;
  return action(loadCatalog);
}
async function viewOrder(order) {
  if (activeOrder.value?.id === order.id) { activeOrder.value = null; return; }
  await action(async () => {
    activeOrder.value = await api("orders/" + order.id);
    await nextTick();
    document.querySelector(".order-detail")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}
async function viewProduct(item) {
  await action(async () => {
    currentProduct.value = await api("catalog/" + item.id);
    loadImage(String(item.id));
  });
}
function add(item) {
  clearStatus();
  confirmed.value = null;
  const pick = picks[item.id];
  const unit = item.units.find((entry) => entry.id === pick.unit);
  const qty = Number(pick.quantity);
  if (!unit || !Number.isInteger(qty) || qty < 1) return;
  const current = cart.value.find((line) => line.product === item.id && line.unit === unit.id);
  if (current) current.quantity += qty;
  else cart.value.push({ product: item.id, unit: unit.id, quantity: qty, name: item.name, unit_name: unit.name });
  status.value = text("added");
}
async function loadOrders(more = false) {
  const params = new URLSearchParams({ offset: more ? orders.value.length : 0 });
  if (orderQuery.value.trim()) params.set("search", orderQuery.value.trim());
  const result = await api("orders?" + params);
  orders.value = more ? [...orders.value, ...result.items] : result.items;
  moreOrders.value = result.has_more;
}
// Confirmation and shipment happen in the back office, so the open order list re-reads itself:
// on pull, when the app returns to the foreground, and every 30 seconds while it stays on screen.
async function refreshOrders() {
  try {
    const params = new URLSearchParams({ offset: 0, limit: Math.min(40, Math.max(20, orders.value.length)) });
    if (orderQuery.value.trim()) params.set("search", orderQuery.value.trim());
    const result = await api("orders?" + params);
    const open = activeOrder.value && result.items.some((row) => row.id === activeOrder.value.id);
    const detail = open ? await api("orders/" + activeOrder.value.id) : null;
    if (page.value !== "history") return;
    orders.value = result.items;
    moreOrders.value = result.has_more;
    if (activeOrder.value) activeOrder.value = detail;
  } catch {
    // A failed background refresh keeps the list already on screen.
  } finally {
    refreshing.value = false;
  }
}
function refreshVisibleOrders() {
  if (device.value && page.value === "history" && document.visibilityState === "visible" && !busy.value && !refreshing.value) {
    refreshOrders();
  }
}
async function loadAddresses() {
  addresses.value = (await api("addresses")).items;
  if (!address.value && !pending.value) address.value = addresses.value[0]?.id || null;
}
async function openPage(next) {
  clearStatus();
  page.value = next;
  currentProduct.value = null;
  quick.value = null;
  if (next !== "history") activeOrder.value = null;
  if (next === "catalog") {
    await Promise.all([loadCatalog(), api("categories").then((data) => { categories.value = data.items; })]);
  } else if (next === "history") {
    confirmed.value = null;
    await loadOrders();
  } else if (next === "cart") {
    await loadAddresses();
  } else if (next === "account") {
    await Promise.all([
      // An older server without the profile endpoint still gets the page, with the sign-in name only.
      api("profile").then((data) => { profile.value = data; }, (error) => { if (error.status !== 404) throw error; }),
      settings.value.capabilities.includes("addresses") ? loadAddresses() : null,
    ]);
  }
}
function checkoutData() {
  return {
    lines: cart.value.map(({ product, unit, quantity }) => ({ product, unit, quantity: Number(quantity) })),
    ...(address.value ? { address: address.value } : {}),
    ...(note.value ? { note: note.value } : {}),
  };
}
async function review() {
  await action(async () => { preview.value = await api("preview", { data: checkoutData() }); });
}
async function submit() {
  if (!compatible.value || !preview.value && !pending.value) return;
  await action(async () => {
    pending.value ||= {
      ...checkoutData(), preview: preview.value.preview, request_id: crypto.randomUUID(),
      accept_backorder: acceptBackorder.value,
    };
    try {
      confirmed.value = await api("orders", { data: pending.value });
      cart.value = [];
      note.value = "";
    } catch (error) {
      if ([400, 409, 422].includes(error.status)) clearPreview();
      throw error;
    }
  });
}
async function openAddress() {
  await action(async () => {
    countries.value = (await api("countries")).items;
    showAddress.value = true;
  });
}
async function selectCountry() {
  addressForm.state = "";
  states.value = addressForm.country ? (await api("states?country=" + addressForm.country)).items : [];
}
async function saveAddress() {
  await action(async () => {
    const { state, ...values } = addressForm;
    const result = await api("addresses", { data: { ...values, ...(state ? { state } : {}) } });
    addresses.value.push(result);
    address.value = result.id;
    showAddress.value = false;
    Object.keys(addressForm).forEach((key) => { addressForm[key] = ""; });
  });
}
async function repeat(order) {
  await action(async () => {
    const result = await api("orders/" + order.id + "/repeat");
    confirmed.value = null;
    cart.value = result.lines;
    await openPage("cart");
    errors.value = result.notices;
    if (cart.value.length && !result.notices.some((item) => item.code === "INSUFFICIENT_STOCK")) {
      preview.value = await api("preview", { data: checkoutData() });
    }
    if (cart.value.length) status.value = text("continueCart");
  });
}
async function download(order) {
  await action(async () => {
    const result = await api("orders/" + order.id + "/document");
    const content = Uint8Array.from(atob(result.pdf), (letter) => letter.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([content], { type: "application/pdf" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = result.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  });
}
onMounted(async () => {
  ordersTimer = setInterval(refreshVisibleOrders, 30000);
  document.addEventListener("visibilitychange", refreshVisibleOrders);
  await action(async () => {
    settings.value = embeddedSettings() ?? await request("settings");
    document.title = settings.value.name;
    if (!localStorage.getItem(originKey + ":language")) {
      const preferred = settings.value.language.split(/[_-]/)[0];
      lang.value = preferred in languages ? preferred : "en";
    }
    document.documentElement.lang = lang.value;
    if (device.value) {
      const restoredCart = read(localStorage, cartKey.value, []);
      const restoredPending = read(localStorage, cartKey.value + ":submission", null);
      cart.value = restoredCart;
      pending.value = restoredPending;
      await openPage(restoredPending ? "cart" : "catalog");
    } else if (sameOrigin && new URLSearchParams(location.search).has("native")) {
      const result = await nativeDevice(false);
      if (result) await enter(result, "native", false);
    }
  });
});
</script>

<template>
  <div ref="shell" class="shell" :class="{ guest: !device }" :data-theme="preset || undefined" :style="theme">
    <header class="masthead">
      <div class="masthead-bar">
        <button v-if="device && hasCatalog && page === 'catalog' && !currentProduct" class="search-toggle" type="button"
          :aria-label="text('search')" :aria-expanded="searchOpen" @click="searchOpen = !searchOpen"><Glyph name="search" /></button>
        <div class="brand">
          <img v-if="logo" :src="logo" alt="" width="36" height="36">
          <strong>{{ settings?.name || text("ordering") }}</strong>
        </div>
        <select v-model="lang" class="language" aria-label="Language" @change="changeLanguage">
          <option v-for="(name, code) in languages" :key="code" :value="code">{{ name }}</option>
        </select>
      </div>
      <p v-if="!device && settings" class="tagline">{{ text("ordering") }}</p>
      <form v-if="device && hasCatalog && page === 'catalog' && !currentProduct" class="searchbar" :class="{ open: searchOpen }" role="search"
        @submit.prevent="action(loadCatalog)">
        <Glyph name="search" :size="20" />
        <input v-model="query" type="search" enterkeyhint="search" :placeholder="text('search')" :aria-label="text('search')">
        <button type="submit">{{ text("view") }}</button>
      </form>
    </header>
    <main :aria-busy="busy">
      <div v-if="!compatible && settings" class="notice caution" role="alert">{{ text("mismatch") }}</div>
      <div v-if="status || errors.length" class="notice" :class="{ caution: failed || errors.length }" role="status">
        <div v-if="status">{{ status }}</div>
        <ul v-if="errors.length">
          <li v-for="(error, index) in errors" :key="index">{{ error.message }}</li>
        </ul>
      </div>
      <section v-if="!device" class="login sheet">
        <h1>{{ text("login") }}</h1>
        <form @submit.prevent="signIn">
          <label>{{ text("loginName") }}<input v-model="login.login" autocomplete="username" autocapitalize="none" required></label>
          <label>{{ text("password") }}<input v-model="login.password" type="password" autocomplete="current-password" required></label>
          <label v-if="askCode">{{ text("code") }}<input v-model="login.totp" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" required></label>
          <label class="check"><input v-model="login.remember" type="checkbox">{{ text("remember") }}</label>
          <van-button native-type="submit" block type="primary" :loading="busy" :disabled="!settings">{{ text("login") }}</van-button>
          <a v-if="nativeLogin" :href="absolute('/web/login?redirect=/b2b?native=1')">{{ text("native") }}</a>
          <p class="muted">{{ text("loginHelp") }}</p>
        </form>
      </section>
      <template v-else-if="hasCatalog">
        <div v-if="settings.banner && page === 'catalog' && !currentProduct" class="banner"><img :src="absolute(settings.banner)" alt=""></div>
        <section v-if="page === 'catalog'">
          <article v-if="currentProduct" class="sheet detail">
            <button class="back" type="button" @click="currentProduct = null"><Glyph name="back" :size="20" />{{ text("back") }}</button>
            <div class="detail-photo"><img v-if="imageUrls[currentProduct.id]" :src="imageUrls[currentProduct.id]" :alt="currentProduct.name"></div>
            <h1>{{ currentProduct.name }}</h1>
            <p v-if="currentProduct.reference" class="reference num">{{ currentProduct.reference }}</p>
            <p v-if="currentProduct.description">{{ currentProduct.description }}</p>
            <p v-if="currentProduct.stock.quantity !== undefined" class="stock" :class="{ out: currentProduct.stock.quantity <= 0 }">{{ text(currentProduct.stock.quantity > 0 ? "stock" : "noStock") }} <span v-if="currentProduct.stock.quantity > 0" class="num">{{ currentProduct.stock.quantity }}</span></p>
            <p v-else-if="currentProduct.stock.available !== undefined" class="stock" :class="{ out: !currentProduct.stock.available }">{{ text(currentProduct.stock.available ? "yesStock" : "noStock") }}</p>
            <ul class="unit-prices"><li v-for="unit in currentProduct.units" :key="unit.id">
              <div>{{ unit.name }}</div><strong class="num">{{ money(unit.price, currentProduct.currency) }}</strong>
              <van-button type="primary" size="small" @click="picks[currentProduct.id] = { unit: unit.id, quantity: 1 }; add(currentProduct)">{{ text("add") }}</van-button>
            </li></ul>
          </article>
          <template v-else>
          <h1 class="sr-only">{{ text("catalog") }}</h1>
          <div v-if="categories.length" class="chips" role="group" :aria-label="text('all')">
            <button type="button" :aria-pressed="category === ''" @click="pickCategory('')"><span class="chip-mark" aria-hidden="true">{{ text("all").slice(0, 1) }}</span>{{ text("all") }}</button>
            <button v-for="row in categories" :key="row.id" type="button" :aria-pressed="category === row.id" @click="pickCategory(row.id)"><span class="chip-mark" aria-hidden="true">{{ row.name.slice(0, 1) }}</span>{{ row.name }}</button>
          </div>
          <div ref="productGrid" class="products">
            <article v-for="item in products" :key="item.id" class="product">
              <div class="product-side">
                <div class="photo" :data-product-image="item.id" @click="viewProduct(item)">
                  <img v-if="imageUrls[item.id]" :src="imageUrls[item.id]" :alt="item.name" loading="lazy">
                  <button class="quick on-photo" :class="{ has: inCart[item.id] }" type="button" :aria-label="text('add') + ' ' + item.name" @click.stop="openQuick(item)">
                    <Glyph name="plus" /><span v-if="inCart[item.id]" class="quick-count num">{{ inCart[item.id] }}</span>
                  </button>
                </div>
                <p v-if="item.stock.quantity !== undefined" class="stock" :class="{ out: item.stock.quantity <= 0 }">{{ text(item.stock.quantity > 0 ? "stock" : "noStock") }} <span v-if="item.stock.quantity > 0" class="num">{{ item.stock.quantity }}</span></p>
                <p v-else-if="item.stock.available !== undefined" class="stock" :class="{ out: !item.stock.available }">{{ text(item.stock.available ? "yesStock" : "noStock") }}</p>
              </div>
              <div class="product-body">
                <h2><button class="product-title" type="button" @click="viewProduct(item)">{{ item.name }}</button></h2>
                <div class="offer">
                  <p class="price"><span class="num">{{ money(unitOf(item)?.price ?? 0, item.currency) }}</span><small v-if="unitOf(item)">/ {{ unitOf(item).name }}</small></p>
                  <button class="quick in-row" :class="{ has: inCart[item.id] }" type="button" :aria-label="text('add') + ' ' + item.name" @click="openQuick(item)">
                    <Glyph name="plus" /><span v-if="inCart[item.id]" class="quick-count num">{{ inCart[item.id] }}</span>
                  </button>
                </div>
                <div v-if="picks[item.id]" class="product-actions">
                  <div v-if="item.units.length > 1" class="units" role="radiogroup" :aria-label="text('unit')">
                    <button v-for="unit in item.units" :key="unit.id" type="button" role="radio" :aria-checked="picks[item.id].unit === unit.id"
                      @click="picks[item.id].unit = unit.id">{{ unit.name }}</button>
                  </div>
                  <div class="buy">
                    <div class="stepper">
                      <button type="button" :aria-label="text('minus')" @click="step(picks[item.id], -1)">−</button>
                      <input v-model.number="picks[item.id].quantity" type="number" inputmode="numeric" min="1" max="100000" :aria-label="text('quantity')">
                      <button type="button" :aria-label="text('plus')" @click="step(picks[item.id], 1)">+</button>
                    </div>
                    <van-button type="primary" size="small" @click="add(item)">{{ text("add") }}</van-button>
                  </div>
                </div>
              </div>
            </article>
          </div>
          <p v-if="!products.length && !busy" class="empty">{{ text("noItems") }}</p>
          <van-button v-if="moreProducts" class="more" block @click="action(() => loadCatalog(true))">{{ text("more") }}</van-button>
          </template>
        </section>
        <section v-else-if="page === 'cart'">
          <h1>{{ text("cart") }}</h1>
          <div v-if="confirmed" class="sheet receipt" role="status">
            <p class="stamp"><span>{{ text("submitted") }}</span><strong class="num">{{ confirmed.name }}</strong></p>
            <van-button size="small" @click="action(() => openPage('catalog'))">{{ text("back") }}</van-button>
          </div>
          <div v-if="pending" class="sheet retry">
            <van-button type="primary" block :loading="busy" @click="submit">{{ text("retry") }}</van-button>
          </div>
          <template v-if="cart.length">
            <ul class="sheet lines">
              <li v-for="(line, index) in cart" :key="line.product + ':' + line.unit">
                <div class="line-name"><strong>{{ line.name }}</strong><small>{{ line.unit_name }}</small></div>
                <div class="stepper">
                  <button type="button" :aria-label="text('minus')" @click="step(line, -1)">−</button>
                  <input v-model.number="line.quantity" type="number" inputmode="numeric" min="1" max="100000" :aria-label="text('quantity')">
                  <button type="button" :aria-label="text('plus')" @click="step(line, 1)">+</button>
                </div>
                <button class="remove" type="button" :aria-label="text('remove')" @click="cart.splice(index, 1)"><Glyph name="remove" :size="20" /></button>
              </li>
            </ul>
            <div class="sheet checkout">
              <label>{{ text("address") }}<select v-model="address">
                <option v-for="entry in addresses" :key="entry.id" :value="entry.id">{{ entry.name }}, {{ entry.display }}</option>
              </select></label>
              <button class="plain" type="button" @click="openAddress">{{ text("addAddress") }}</button>
              <form v-if="showAddress" class="address-form" @submit.prevent="saveAddress">
                <label>{{ text("addressName") }}<input v-model="addressForm.name" required></label>
                <label>{{ text("street") }}<input v-model="addressForm.street" required></label>
                <label>{{ text("city") }}<input v-model="addressForm.city" required></label>
                <label>{{ text("country") }}<select v-model="addressForm.country" required @change="action(selectCountry)">
                  <option v-for="row in countries" :key="row.code" :value="row.code">{{ row.name }}</option>
                </select></label>
                <label v-if="states.length">{{ text("state") }}<select v-model="addressForm.state" :required="selectedCountry?.state_required">
                  <option v-for="row in states" :key="row.id" :value="row.id">{{ row.name }}</option>
                </select></label>
                <label>{{ text("zip") }}<input v-model="addressForm.zip" :required="selectedCountry?.zip_required"></label>
                <div class="inline">
                  <van-button native-type="submit" size="small" type="primary">{{ text("save") }}</van-button>
                  <van-button size="small" @click="showAddress = false">{{ text("cancel") }}</van-button>
                </div>
              </form>
              <label>{{ text("notes") }}<textarea v-model="note" maxlength="2000" rows="2"></textarea></label>
              <dl v-if="settings.delivery || settings.payment" class="terms">
                <template v-if="settings.delivery"><dt>{{ text("delivery") }}</dt><dd>{{ settings.delivery }}</dd></template>
                <template v-if="settings.payment"><dt>{{ text("payment") }}</dt><dd>{{ settings.payment }}</dd></template>
              </dl>
              <van-button v-if="!preview && !pending" type="primary" block :disabled="!compatible" :loading="busy" @click="review">{{ text("checkout") }}</van-button>
              <template v-if="preview">
                <ul class="slip-lines">
                  <li v-for="line in preview.lines" :key="line.product + ':' + line.unit">
                    <div>{{ line.name }}<small>{{ line.quantity }} × {{ line.unit_name }}</small></div>
                    <strong class="num">{{ money(line.total, preview.currency) }}</strong>
                  </li>
                </ul>
                <dl class="totals">
                  <dt>{{ text("subtotal") }}</dt><dd class="num">{{ money(preview.untaxed, preview.currency) }}</dd>
                  <dt>{{ text("tax") }}</dt><dd class="num">{{ money(preview.tax, preview.currency) }}</dd>
                  <dt class="grand">{{ text("total") }}</dt><dd class="grand num">{{ money(preview.total, preview.currency) }}</dd>
                </dl>
                <ul v-if="preview.notices.length" class="cautions">
                  <li v-for="row in preview.notices" :key="row.field">{{ row.message }}</li>
                </ul>
                <label v-if="preview.notices.length" class="check"><input v-model="acceptBackorder" type="checkbox">{{ text("pending") }}</label>
                <van-button v-if="!pending" type="primary" block :disabled="!compatible || preview.notices.length && !acceptBackorder" :loading="busy" @click="submit">{{ text("place") }}</van-button>
              </template>
            </div>
          </template>
          <div v-else-if="!confirmed" class="empty">
            <p class="muted">{{ text("noCart") }}</p>
            <van-button size="small" @click="action(() => openPage('catalog'))">{{ text("back") }}</van-button>
          </div>
        </section>
        <section v-else-if="page === 'history'">
          <h1>{{ text("history") }}</h1>
          <form class="searchbar" role="search" @submit.prevent="action(loadOrders)">
            <Glyph name="search" :size="20" />
            <input v-model="orderQuery" type="search" enterkeyhint="search" :placeholder="text('searchOrders')" :aria-label="text('searchOrders')">
            <button type="submit">{{ text("view") }}</button>
          </form>
          <van-pull-refresh v-model="refreshing" class="orders-pull" :pulling-text="text('pull')" :loosing-text="text('release')"
            :loading-text="text('refreshing')" @refresh="refreshOrders">
          <ul v-if="orders.length" class="sheet orders">
            <li v-for="order in orders" :key="order.id">
              <div class="order-row">
                <div class="order-main">
                  <strong class="num">{{ order.name }}</strong>
                  <small>
                    <span class="state" :data-state="order.state">{{ stateName(order.state) }}</span>
                    <span v-if="order.delivery" class="state" :data-delivery="order.delivery">{{ deliveryName(order.delivery) }}</span>
                    {{ order.date.slice(0, 10) }}
                  </small>
                </div>
                <strong class="num">{{ money(order.total, order.currency) }}</strong>
                <button class="plain" type="button" :aria-expanded="activeOrder?.id === order.id" @click="viewOrder(order)">{{ text("view") }}</button>
              </div>
              <div v-if="activeOrder?.id === order.id" class="order-detail">
                <ul class="slip-lines"><li v-for="line in activeOrder.lines" :key="line.product + ':' + line.unit">
                  <div>{{ line.name }}<small>{{ line.quantity }} × {{ line.unit_name }}<span v-if="line.delivered !== undefined" class="shipped">{{ text("shippedQty") }} {{ line.delivered }}</span></small></div>
                  <strong class="num">{{ money(line.total, activeOrder.currency) }}</strong>
                </li></ul>
                <div class="inline">
                  <van-button type="primary" size="small" @click="repeat(activeOrder)">{{ text("refill") }}</van-button>
                  <van-button size="small" @click="download(activeOrder)">{{ text("pdf") }}</van-button>
                </div>
              </div>
            </li>
          </ul>
          <p v-else-if="!busy" class="empty">{{ text("noOrders") }}</p>
          </van-pull-refresh>
          <van-button v-if="moreOrders" class="more" block @click="action(() => loadOrders(true))">{{ text("more") }}</van-button>
        </section>
        <section v-else class="account">
          <h1 class="sr-only">{{ text("account") }}</h1>
          <div class="sheet profile">
            <div class="avatar" aria-hidden="true">{{ (profile?.name || device.account).slice(0, 1).toUpperCase() }}</div>
            <div>
              <h2>{{ profile?.name || device.account }}</h2>
              <p v-if="profile?.company">{{ profile.company }}</p>
            </div>
          </div>
          <dl v-if="profile" class="sheet facts">
            <dt>{{ text("account") }}</dt><dd>{{ profile.login }}</dd>
            <template v-if="profile.email && profile.email !== profile.login"><dt>{{ text("email") }}</dt><dd>{{ profile.email }}</dd></template>
            <template v-if="profile.phone"><dt>{{ text("phone") }}</dt><dd>{{ profile.phone }}</dd></template>
          </dl>
          <div v-if="profile?.salesperson" class="sheet card">
            <p class="card-title">{{ text("yourSales") }}</p>
            <strong>{{ profile.salesperson.name }}</strong>
            <div v-if="profile.salesperson.phone || profile.salesperson.email" class="inline">
              <a v-if="profile.salesperson.phone" class="pill" :href="'tel:' + profile.salesperson.phone"><Glyph name="phone" :size="18" />{{ profile.salesperson.phone }}</a>
              <a v-if="profile.salesperson.email" class="pill" :href="'mailto:' + profile.salesperson.email"><Glyph name="mail" :size="18" />{{ profile.salesperson.email }}</a>
            </div>
          </div>
          <div v-if="settings.capabilities.includes('addresses')" class="sheet card">
            <p class="card-title">{{ text("address") }}</p>
            <ul v-if="addresses.length" class="address-list">
              <li v-for="entry in addresses" :key="entry.id"><strong>{{ entry.name }}</strong><small>{{ entry.display }}</small></li>
            </ul>
            <p v-else class="muted">{{ text("noAddress") }}</p>
          </div>
          <details class="sheet security">
            <summary>{{ text("changePassword") }}</summary>
            <form @submit.prevent="changePassword">
              <label>{{ text("currentPassword") }}<input v-model="password.current_password" type="password" autocomplete="current-password" required></label>
              <label>{{ text("newPassword") }}<input v-model="password.new_password" type="password" autocomplete="new-password" required></label>
              <van-button native-type="submit" type="primary" block :loading="busy">{{ text("changePassword") }}</van-button>
            </form>
          </details>
          <van-button class="sign-out" block :loading="busy" @click="signOut">{{ text("logout") }}</van-button>
          <p class="account-foot"><span v-if="deviceUntil">{{ text("deviceUntil") }} {{ deviceUntil }}</span><span>{{ text("version") }} {{ version }}</span></p>
        </section>
      </template>
      <p v-else class="empty">{{ text("unavailable") }}</p>
    </main>
    <div v-if="quick && picks[quick.id]" class="scrim" @click.self="quick = null">
      <section class="sheet quick-sheet" role="dialog" aria-modal="true" :aria-label="quick.name" tabindex="-1" @keydown.esc="quick = null">
        <div class="quick-head">
          <div class="photo"><img v-if="imageUrls[quick.id]" :src="imageUrls[quick.id]" alt=""></div>
          <div>
            <h2>{{ quick.name }}</h2>
            <p class="price"><span class="num">{{ money(unitOf(quick)?.price ?? 0, quick.currency) }}</span><small v-if="unitOf(quick)">/ {{ unitOf(quick).name }}</small></p>
          </div>
          <button class="remove" type="button" :aria-label="text('cancel')" @click="quick = null"><Glyph name="remove" :size="20" /></button>
        </div>
        <div v-if="quick.units.length > 1" class="units" role="radiogroup" :aria-label="text('unit')">
          <button v-for="unit in quick.units" :key="unit.id" type="button" role="radio" :aria-checked="picks[quick.id].unit === unit.id"
            @click="picks[quick.id].unit = unit.id">{{ unit.name }}</button>
        </div>
        <div class="buy">
          <div class="stepper">
            <button type="button" :aria-label="text('minus')" @click="step(picks[quick.id], -1)">−</button>
            <input v-model.number="picks[quick.id].quantity" type="number" inputmode="numeric" min="1" max="100000" :aria-label="text('quantity')">
            <button type="button" :aria-label="text('plus')" @click="step(picks[quick.id], 1)">+</button>
          </div>
          <van-button type="primary" @click="addQuick">{{ text("add") }}</van-button>
        </div>
      </section>
    </div>
    <nav v-if="device && hasCatalog" class="tabs" :aria-label="text('ordering')">
      <button v-for="tab in ['catalog', 'cart', 'history', 'account']" :key="tab" :aria-current="page === tab ? 'page' : undefined"
        @click="action(() => openPage(tab))"><Glyph :name="tab" />{{ text(tab) }}<span v-if="tab === 'cart' && count" class="count">{{ count }}</span></button>
    </nav>
  </div>
</template>

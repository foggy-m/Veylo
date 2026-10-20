/* Minimal, test-only DOM adapter for dependency-free interaction tests.
 * Not a browser, renderer or standards-complete DOM. Layout/CSS is tested separately in ui-demo.html.
 */
'use strict';

class TestEvent {
  constructor(type, options = {}) {
    this.type = type;
    Object.assign(this, options);
    this.bubbles = options.bubbles === true;
    this.defaultPrevented = false;
    this.cancelBubble = false;
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.cancelBubble = true; }
}
class TestEventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, callback, options = {}) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push({ callback, once: options.once === true });
  }
  removeEventListener(name, callback) {
    this.listeners.set(name, (this.listeners.get(name) || []).filter(entry => entry.callback !== callback));
  }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    event.currentTarget = this;
    for (const entry of [...(this.listeners.get(event.type) || [])]) {
      entry.callback.call(this, event);
      if (entry.once) this.removeEventListener(event.type, entry.callback);
    }
    if (event.bubbles && !event.cancelBubble) this.parentNode?.dispatchEvent(event);
    return !event.defaultPrevented;
  }
}
class TestNode extends TestEventTarget {
  constructor(document, nodeType) {
    super();
    this.ownerDocument = document;
    this.nodeType = nodeType;
    this.childNodes = [];
    this.parentNode = null;
  }
  get children() { return this.childNodes.filter(node => node.nodeType === 1); }
  get firstElementChild() { return this.children[0] || null; }
  get parentElement() { return this.parentNode?.nodeType === 1 ? this.parentNode : null; }
  appendChild(node) {
    if (node.parentNode) node.parentNode.removeChild(node);
    this.childNodes.push(node);
    node.parentNode = this;
    return node;
  }
  append(...nodes) {
    for (const node of nodes) this.appendChild(typeof node === 'string' ? this.ownerDocument.createTextNode(node) : node);
  }
  removeChild(node) {
    const index = this.childNodes.indexOf(node);
    if (index !== -1) this.childNodes.splice(index, 1);
    node.parentNode = null;
    return node;
  }
  replaceChildren(...nodes) {
    for (const node of this.childNodes) node.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }
  remove() { this.parentNode?.removeChild(this); }
  contains(node) { return node === this || this.childNodes.some(child => child.contains?.(node)); }
  getRootNode() {
    let root = this;
    while (root.parentNode) root = root.parentNode;
    return root;
  }
  get isConnected() {
    const root = this.getRootNode();
    return root.nodeType === 9 || root.host?.isConnected || false;
  }
  get textContent() { return this.childNodes.map(node => node.textContent).join(''); }
  set textContent(value) { this.replaceChildren(this.ownerDocument.createTextNode(String(value ?? ''))); }
  querySelectorAll(selector) {
    const found = [];
    const visit = node => {
      for (const child of node.children) {
        if (child.matches(selector)) found.push(child);
        visit(child);
      }
    };
    visit(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
class TestText extends TestNode {
  constructor(document, text) { super(document, 3); this.data = text; }
  get textContent() { return this.data; }
  set textContent(value) { this.data = String(value); }
}
class TestStyle {
  constructor() { this.values = new Map(); }
  setProperty(name, value, priority = '') { this.values.set(name, { value: String(value), priority }); }
  getPropertyValue(name) { return this.values.get(name)?.value || ''; }
  getPropertyPriority(name) { return this.values.get(name)?.priority || ''; }
  removeProperty(name) { const value = this.getPropertyValue(name); this.values.delete(name); return value; }
}
const dataName = name => `data-${String(name).replace(/[A-Z]/g, value => `-${value.toLowerCase()}`)}`;
class TestElement extends TestNode {
  constructor(document, tagName) {
    super(document, 1);
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.style = new TestStyle();
    this._value = '';
    this._checked = false;
    this.scrollTop = 0;
    this.dataset = new Proxy({}, {
      get: (_target, name) => this.getAttribute(dataName(name)) ?? undefined,
      set: (_target, name, value) => { this.setAttribute(dataName(name), value); return true; },
      deleteProperty: (_target, name) => { this.removeAttribute(dataName(name)); return true; }
    });
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === 'value') this._value = String(value);
    if (name === 'checked') this._checked = true;
  }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  get id() { return this.getAttribute('id') || ''; }
  set id(value) { this.setAttribute('id', value); }
  get className() { return this.getAttribute('class') || ''; }
  set className(value) { this.setAttribute('class', value); }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(value) { if (value) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }
  get disabled() { return this.hasAttribute('disabled'); }
  set disabled(value) { if (value) this.setAttribute('disabled', ''); else this.removeAttribute('disabled'); }
  get open() { return this.hasAttribute('open'); }
  set open(value) { if (value) this.setAttribute('open', ''); else this.removeAttribute('open'); }
  get checked() { return this._checked; }
  set checked(value) { this._checked = value === true; }
  get title() { return this.getAttribute('title') || ''; }
  set title(value) { this.setAttribute('title', value); }
  get type() { return this.getAttribute('type') || ''; }
  set type(value) { this.setAttribute('type', value); }
  get tabIndex() { return Number(this.getAttribute('tabindex') || 0); }
  set tabIndex(value) { this.setAttribute('tabindex', value); }
  get options() { return this.tagName === 'SELECT' ? this.querySelectorAll('option') : undefined; }
  get value() {
    if (this.tagName === 'SELECT') {
      const options = this.options;
      if (!this._selectedSet) return options.find(option => option.hasAttribute('selected'))?.value ?? options[0]?.value ?? '';
      return options.some(option => option.value === this._value) ? this._value : '';
    }
    if (this.tagName === 'OPTION' && !this.hasAttribute('value') && !this._value) return this.textContent;
    return this._value;
  }
  set value(value) { this._value = String(value); this._selectedSet = true; }
  get innerHTML() { return this.textContent; }
  set innerHTML(value) {
    this.replaceChildren();
    parseHTML(String(value), this);
  }
  matches(selector) {
    const trimmed = selector.trim();
    if (trimmed.includes(',')) return trimmed.split(',').some(part => this.matches(part));
    const tag = trimmed.match(/^[a-zA-Z][\w-]*/)?.[0];
    if (tag && tag.toUpperCase() !== this.tagName) return false;
    const id = trimmed.match(/#([\w-]+)/)?.[1];
    if (id && id !== this.id) return false;
    for (const match of trimmed.matchAll(/\.([\w-]+)/g)) {
      if (!this.className.split(/\s+/).includes(match[1])) return false;
    }
    for (const match of trimmed.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)) {
      if (!this.hasAttribute(match[1])) return false;
      if (match[2] !== undefined && this.getAttribute(match[1]) !== match[2]) return false;
    }
    return true;
  }
  closest(selector) {
    let current = this;
    while (current?.nodeType === 1) { if (current.matches(selector)) return current; current = current.parentNode; }
    return null;
  }
  click() {
    if (this.disabled) return;
    if (this.tagName === 'INPUT' && this.type === 'checkbox') this.checked = !this.checked;
    this.dispatchEvent(new TestEvent('click', { bubbles: true }));
  }
  focus() {
    const root = this.getRootNode();
    if (root.host) { root.activeElement = this; this.ownerDocument.activeElement = root.host; }
    else this.ownerDocument.activeElement = this;
  }
  attachShadow() {
    this.shadowRoot = new TestShadowRoot(this.ownerDocument, this);
    return this.shadowRoot;
  }
}
class TestShadowRoot extends TestNode {
  constructor(document, host) { super(document, 11); this.host = host; this.activeElement = null; }
}
class TestDocument extends TestNode {
  constructor() {
    super(null, 9);
    this.ownerDocument = this;
    this.title = 'Synthetic fixture document';
    this.documentElement = this.createElement('html');
    this.body = this.createElement('body');
    this.appendChild(this.documentElement);
    this.documentElement.appendChild(this.body);
    this.activeElement = this.body;
  }
  createElement(tagName) { return new TestElement(this, tagName); }
  createTextNode(value) { return new TestText(this, value); }
  getElementById(id) { return this.querySelector(`#${id}`); }
}
function decode(value) {
  return value.replace(/&(?:amp|lt|gt|quot|apos|#39|#x[0-9a-f]+|#\d+);/gi, entity => {
    const known = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&#39;': "'" };
    if (known[entity]) return known[entity];
    return String.fromCodePoint(parseInt(entity.slice(2, -1).replace(/^x/i, ''), /^&#x/i.test(entity) ? 16 : 10));
  });
}
function parseHTML(html, parent) {
  const stack = [parent];
  const tokens = html.match(/<(?:"[^"]*"|'[^']*'|[^'">])*>|[^<]+/g) || [];
  const voids = new Set(['AREA', 'BASE', 'BR', 'COL', 'EMBED', 'HR', 'IMG', 'INPUT', 'LINK', 'META', 'PARAM', 'SOURCE', 'TRACK', 'WBR']);
  for (const token of tokens) {
    if (token.startsWith('<!--') || token.startsWith('<!')) continue;
    if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
    if (!token.startsWith('<')) { stack[stack.length - 1].appendChild(parent.ownerDocument.createTextNode(decode(token))); continue; }
    const match = token.match(/^<([a-zA-Z][\w:-]*)\s*([\s\S]*?)\/?\s*>$/);
    if (!match) continue;
    const element = parent.ownerDocument.createElement(match[1]);
    const attributes = match[2].matchAll(/([^\s"'=<>`/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g);
    for (const attribute of attributes) element.setAttribute(attribute[1], decode(attribute[2] ?? attribute[3] ?? attribute[4] ?? ''));
    stack[stack.length - 1].appendChild(element);
    if (!voids.has(element.tagName) && !token.endsWith('/>')) stack.push(element);
  }
}
function createDOM() {
  const document = new TestDocument();
  const events = new TestEventTarget();
  const window = {
    document, Event: TestEvent, KeyboardEvent: TestEvent,
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    dispatchEvent: events.dispatchEvent.bind(events)
  };
  return { document, window };
}
module.exports = { createDOM, TestEvent };

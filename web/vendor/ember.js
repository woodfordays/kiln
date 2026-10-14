/* @ds-bundle: {"format":4,"namespace":"Ember","components":[{"name":"Button"},{"name":"IconButton"},{"name":"SegmentedControl"},{"name":"TextField"},{"name":"Select"},{"name":"Checkbox"},{"name":"Switch"},{"name":"Tabs"},{"name":"SideNav"},{"name":"Card"},{"name":"StatCard"},{"name":"Table"},{"name":"Badge"},{"name":"Avatar"},{"name":"Progress"},{"name":"Alert"},{"name":"Toast"},{"name":"Tooltip"},{"name":"Dialog"},{"name":"Spinner"},{"name":"EmptyState"},{"name":"Icon"}]} */
/* Ember design system components. Requires React 18 (window.React, window.ReactDOM). */
(() => {
  var __create = Object.create;
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __getProtoOf = Object.getPrototypeOf;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __commonJS = (cb, mod) => function __require() {
    return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
  };
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
    // If the importer is in node compatibility mode or this is not an ESM
    // file that has been converted to a CommonJS file using a Babel-
    // compatible transform (i.e. "__esModule" has not been set), then set
    // "default" to the CommonJS "module.exports" for node compatibility.
    isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
    mod
  ));

  // g:react
  var require_react = __commonJS({
    "g:react"(exports, module) {
      module.exports = window.React;
    }
  });

  // g:react-dom
  var require_react_dom = __commonJS({
    "g:react-dom"(exports, module) {
      module.exports = window.ReactDOM;
    }
  });

  // ../out/project/components/src/index.ts
  var src_exports = {};
  __export(src_exports, {
    Alert: () => Alert,
    Avatar: () => Avatar,
    Badge: () => Badge,
    Button: () => Button,
    Card: () => Card,
    Checkbox: () => Checkbox,
    Dialog: () => Dialog,
    EmptyState: () => EmptyState,
    ICON_NAMES: () => ICON_NAMES,
    Icon: () => Icon,
    IconButton: () => IconButton,
    Progress: () => Progress,
    SegmentedControl: () => SegmentedControl,
    Select: () => Select,
    SideNav: () => SideNav,
    Spinner: () => Spinner,
    StatCard: () => StatCard,
    Switch: () => Switch,
    Table: () => Table,
    Tabs: () => Tabs,
    TextField: () => TextField,
    Toast: () => Toast,
    ToastRegion: () => ToastRegion,
    Tooltip: () => Tooltip
  });

  // ../out/project/components/src/actions.tsx
  var React4 = __toESM(require_react());

  // ../out/project/components/src/Icon.tsx
  var React2 = __toESM(require_react());

  // ../out/project/components/src/icons.ts
  var ICONS = {
    "grid": [["rect", { "x": 4, "y": 4, "width": 6.5, "height": 6.5, "rx": 1.5 }], ["rect", { "x": 13.5, "y": 4, "width": 6.5, "height": 6.5, "rx": 1.5 }], ["rect", { "x": 4, "y": 13.5, "width": 6.5, "height": 6.5, "rx": 1.5 }], ["rect", { "x": 13.5, "y": 13.5, "width": 6.5, "height": 6.5, "rx": 1.5 }]],
    "activity": [["path", { "d": "M3 12h3.5l2.5-6.5 5 13 2.5-6.5H21" }]],
    "database": [["ellipse", { "cx": 12, "cy": 6, "rx": 7, "ry": 2.75 }], ["path", { "d": "M5 6v6c0 1.52 3.13 2.75 7 2.75s7-1.23 7-2.75V6" }], ["path", { "d": "M5 12v6c0 1.52 3.13 2.75 7 2.75s7-1.23 7-2.75v-6" }]],
    "workflow": [["rect", { "x": 3.5, "y": 3.5, "width": 7, "height": 7, "rx": 1.75 }], ["rect", { "x": 13.5, "y": 13.5, "width": 7, "height": 7, "rx": 1.75 }], ["path", { "d": "M10.5 7h3a3.5 3.5 0 0 1 3.5 3.5v3" }]],
    "message": [["path", { "d": "M6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5h-7L6 20.5V17h-.5A2.5 2.5 0 0 1 3 14.5v-8A2.5 2.5 0 0 1 5.5 4z" }]],
    "file": [["path", { "d": "M14 3.5H7A2.5 2.5 0 0 0 4.5 6v12A2.5 2.5 0 0 0 7 20.5h10a2.5 2.5 0 0 0 2.5-2.5V9z" }], ["path", { "d": "M14 3.5V9h5.5" }], ["path", { "d": "M8.5 13h7M8.5 16.5h4.5" }]],
    "users": [["circle", { "cx": 9.5, "cy": 8, "r": 3.5 }], ["path", { "d": "M3.5 19.5c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" }], ["path", { "d": "M16 4.7a3.5 3.5 0 0 1 0 6.6" }], ["path", { "d": "M18 14.4c1.9.6 3 2.4 3 5.1" }]],
    "sliders": [["path", { "d": "M4 7.5h8M16 7.5h4M4 16.5h3M11 16.5h9" }], ["circle", { "cx": 14, "cy": 7.5, "r": 2 }], ["circle", { "cx": 9, "cy": 16.5, "r": 2 }]],
    "bell": [["path", { "d": "M6.5 15.5V11a5.5 5.5 0 0 1 11 0v4.5l1.5 2.5h-14z" }], ["path", { "d": "M10 20.5a2 2 0 0 0 4 0" }]],
    "server": [["rect", { "x": 3.5, "y": 4, "width": 17, "height": 7, "rx": 2 }], ["rect", { "x": 3.5, "y": 13, "width": 17, "height": 7, "rx": 2 }], ["circle", { "cx": 7.5, "cy": 7.5, "r": 0.55 }], ["circle", { "cx": 7.5, "cy": 16.5, "r": 0.55 }], ["path", { "d": "M12 7.5h4.5M12 16.5h4.5" }]],
    "terminal": [["rect", { "x": 3, "y": 4.5, "width": 18, "height": 15, "rx": 2.5 }], ["path", { "d": "M7.5 9.5l3 2.5-3 2.5" }], ["path", { "d": "M12.5 15h4" }]],
    "sparkle": [["path", { "d": "M12 3.5c.6 4.7 3.8 7.9 8.5 8.5-4.7.6-7.9 3.8-8.5 8.5-.6-4.7-3.8-7.9-8.5-8.5 4.7-.6 7.9-3.8 8.5-8.5z" }]],
    "menu": [["path", { "d": "M4 7h16M4 12h16M4 17h16" }]],
    "search": [["circle", { "cx": 11, "cy": 11, "r": 6.5 }], ["path", { "d": "M16 16l4.5 4.5" }]],
    "plus": [["path", { "d": "M12 5v14M5 12h14" }]],
    "minus": [["path", { "d": "M5 12h14" }]],
    "check": [["path", { "d": "M5 12.5l4.5 4.5L19 7.5" }]],
    "x": [["path", { "d": "M6.5 6.5l11 11M17.5 6.5l-11 11" }]],
    "edit": [["path", { "d": "M14.5 5.5l4 4L9 19H5v-4z" }], ["path", { "d": "M12.5 7.5l4 4" }]],
    "trash": [["path", { "d": "M4.5 7h15" }], ["path", { "d": "M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2" }], ["path", { "d": "M6.5 7l.75 11.6A1.5 1.5 0 0 0 8.75 20h6.5a1.5 1.5 0 0 0 1.5-1.4L17.5 7" }], ["path", { "d": "M10 11v5M14 11v5" }]],
    "copy": [["rect", { "x": 8.5, "y": 8.5, "width": 11.5, "height": 11.5, "rx": 2 }], ["path", { "d": "M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5" }]],
    "download": [["path", { "d": "M12 4v11" }], ["path", { "d": "M7.5 10.5L12 15l4.5-4.5" }], ["path", { "d": "M5 19.5h14" }]],
    "upload": [["path", { "d": "M12 15V4" }], ["path", { "d": "M7.5 8.5L12 4l4.5 4.5" }], ["path", { "d": "M5 19.5h14" }]],
    "refresh": [["path", { "d": "M19.5 12a7.5 7.5 0 1 1-2.2-5.3L19.5 9" }], ["path", { "d": "M19.5 4.5V9H15" }]],
    "external-link": [["path", { "d": "M14 4.5h5.5V10" }], ["path", { "d": "M19.5 4.5L11 13" }], ["path", { "d": "M17.5 14v3.5a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2H10" }]],
    "filter": [["path", { "d": "M4 5.5h16l-6 7v5.5l-4 2v-7.5z" }]],
    "more": [["circle", { "cx": 6, "cy": 12, "r": 0.9 }], ["circle", { "cx": 12, "cy": 12, "r": 0.9 }], ["circle", { "cx": 18, "cy": 12, "r": 0.9 }]],
    "play": [["path", { "d": "M8 5.5v13l10.5-6.5z" }]],
    "pause": [["path", { "d": "M9 5.5v13M15 5.5v13" }]],
    "chevron-down": [["path", { "d": "M6.5 9.5l5.5 5.5 5.5-5.5" }]],
    "chevron-up": [["path", { "d": "M6.5 14.5L12 9l5.5 5.5" }]],
    "chevron-right": [["path", { "d": "M9.5 6.5l5.5 5.5-5.5 5.5" }]],
    "chevron-left": [["path", { "d": "M14.5 6.5L9 12l5.5 5.5" }]],
    "chevrons-up-down": [["path", { "d": "M8 9.5l4-4 4 4M8 14.5l4 4 4-4" }]],
    "arrow-right": [["path", { "d": "M5 12h14M13 6l6 6-6 6" }]],
    "arrow-up-right": [["path", { "d": "M7 17L17 7M8.5 7H17v8.5" }]],
    "arrow-down-right": [["path", { "d": "M7 7l10 10M17 8.5V17H8.5" }]],
    "info": [["circle", { "cx": 12, "cy": 12, "r": 9 }], ["path", { "d": "M12 11v5.5" }], ["circle", { "cx": 12, "cy": 7.9, "r": 0.55 }]],
    "check-circle": [["circle", { "cx": 12, "cy": 12, "r": 9 }], ["path", { "d": "M8.5 12.5l2.5 2.5 4.5-5" }]],
    "alert-triangle": [["path", { "d": "M10.3 4.6a2 2 0 0 1 3.4 0l7 12a2 2 0 0 1-1.7 3H5a2 2 0 0 1-1.7-3z" }], ["path", { "d": "M12 9.5v4" }], ["circle", { "cx": 12, "cy": 16.6, "r": 0.55 }]],
    "alert-circle": [["circle", { "cx": 12, "cy": 12, "r": 9 }], ["path", { "d": "M12 7.5v5.5" }], ["circle", { "cx": 12, "cy": 16.3, "r": 0.55 }]],
    "clock": [["circle", { "cx": 12, "cy": 12, "r": 9 }], ["path", { "d": "M12 7.5V12l3 2" }]],
    "calendar": [["rect", { "x": 3.5, "y": 5, "width": 17, "height": 15.5, "rx": 2.5 }], ["path", { "d": "M3.5 10h17" }], ["path", { "d": "M8 3v4M16 3v4" }]],
    "sun": [["circle", { "cx": 12, "cy": 12, "r": 4 }], ["path", { "d": "M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" }]],
    "moon": [["path", { "d": "M19.5 14.2A7.8 7.8 0 1 1 9.8 4.5a6.3 6.3 0 0 0 9.7 9.7z" }]]
  };
  var ICON_NAMES = Object.keys(ICONS);

  // ../out/project/components/src/util.ts
  var React = __toESM(require_react());
  function cx(...parts) {
    return parts.filter(Boolean).join(" ");
  }
  function useControllable(value, defaultValue, onChange) {
    const [inner, setInner] = React.useState(defaultValue);
    const controlled = value !== void 0;
    const current = controlled ? value : inner;
    const set = React.useCallback(
      (next) => {
        if (!controlled) setInner(next);
        if (onChange) onChange(next);
      },
      [controlled, onChange]
    );
    return [current, set];
  }
  var supportsPopover = typeof HTMLElement !== "undefined" && "popover" in HTMLElement.prototype;
  function useTopLayer(ref, active) {
    React.useLayoutEffect(() => {
      const el = ref.current;
      if (!el || !supportsPopover || !active) return;
      try {
        el.showPopover && el.showPopover();
      } catch (_) {
      }
      return () => {
        try {
          el.hidePopover && el.hidePopover();
        } catch (_) {
        }
      };
    }, [active]);
  }
  function nextIndex(key, current, count, isDisabled, orientation = "horizontal") {
    const forward = orientation === "vertical" ? ["ArrowDown"] : orientation === "both" ? ["ArrowRight", "ArrowDown"] : ["ArrowRight"];
    const back = orientation === "vertical" ? ["ArrowUp"] : orientation === "both" ? ["ArrowLeft", "ArrowUp"] : ["ArrowLeft"];
    let step = 0;
    let i = current;
    if (forward.indexOf(key) >= 0) step = 1;
    else if (back.indexOf(key) >= 0) step = -1;
    else if (key === "Home") {
      i = -1;
      step = 1;
    } else if (key === "End") {
      i = count;
      step = -1;
    } else return -1;
    for (let n = 0; n < count; n++) {
      i = (i + step + count) % count;
      if (!isDisabled(i)) return i;
    }
    return -1;
  }
  function signedPercent(n, digits = 1) {
    const abs = Math.abs(n).toFixed(digits);
    return n > 0 ? "+" + abs + "%" : n < 0 ? "−" + abs + "%" : abs + "%";
  }

  // ../out/project/components/src/Icon.tsx
  function Icon({ name, size = 20, strokeWidth, label, className, ...rest }) {
    const shapes = ICONS[name];
    if (!shapes) return null;
    const sw = strokeWidth != null ? strokeWidth : size <= 16 ? 2 : 1.75;
    return /* @__PURE__ */ React2.createElement(
      "svg",
      {
        viewBox: "0 0 24 24",
        width: size,
        height: size,
        fill: "none",
        stroke: "currentColor",
        strokeWidth: sw,
        strokeLinecap: "round",
        strokeLinejoin: "round",
        className: cx("em-icon", className),
        role: label ? "img" : void 0,
        "aria-label": label,
        "aria-hidden": label ? void 0 : true,
        focusable: "false",
        ...rest
      },
      shapes.map(([tag, attrs], i) => React2.createElement(tag, { key: i, ...attrs }))
    );
  }

  // ../out/project/components/src/feedback.tsx
  var React3 = __toESM(require_react());
  var ReactDOM = __toESM(require_react_dom());
  var TONE_ICON = {
    info: "info",
    success: "check-circle",
    warning: "alert-triangle",
    danger: "alert-circle"
  };
  function CloseButton({ label, onClick, className }) {
    return /* @__PURE__ */ React3.createElement("button", { type: "button", "aria-label": label, onClick, className: cx("em-btn em-btn--ghost em-btn--sm em-btn--icon", className) }, /* @__PURE__ */ React3.createElement(Icon, { name: "x", size: 16 }));
  }
  function Spinner({ size = 20, label, tone = "ember", className }) {
    return /* @__PURE__ */ React3.createElement(
      "span",
      {
        className: cx("em-spinner", "em-spinner--" + tone, className),
        role: label ? "status" : void 0,
        "aria-label": label,
        "aria-hidden": label ? void 0 : true,
        style: { width: size, height: size }
      },
      /* @__PURE__ */ React3.createElement("svg", { viewBox: "0 0 24 24", width: size, height: size, fill: "none", strokeWidth: 2.5 }, /* @__PURE__ */ React3.createElement("circle", { className: "em-spinner__track", cx: "12", cy: "12", r: "9" }), /* @__PURE__ */ React3.createElement("path", { className: "em-spinner__arc", d: "M12 3a9 9 0 0 1 9 9", strokeLinecap: "round" }))
    );
  }
  function Tooltip({ content, children, side = "top", open: openProp, delay = 350, describe = true }) {
    const id = React3.useId();
    const anchorRef = React3.useRef(null);
    const tipRef = React3.useRef(null);
    const [openState, setOpenState] = React3.useState(false);
    const open = openProp !== void 0 ? openProp : openState;
    const timers = React3.useRef({});
    const [pos, setPos] = React3.useState(null);
    const clear = () => {
      window.clearTimeout(timers.current.show);
      window.clearTimeout(timers.current.hide);
    };
    const show = (now) => {
      clear();
      if (now) setOpenState(true);
      else timers.current.show = window.setTimeout(() => setOpenState(true), delay);
    };
    const hide = () => {
      clear();
      timers.current.hide = window.setTimeout(() => setOpenState(false), 120);
    };
    React3.useEffect(() => clear, []);
    React3.useEffect(() => {
      if (!open || openProp !== void 0) return;
      const onKey = (e) => {
        if (e.key === "Escape") setOpenState(false);
      };
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }, [open, openProp]);
    useTopLayer(tipRef, open);
    React3.useLayoutEffect(() => {
      if (!open) {
        setPos(null);
        return;
      }
      const place = () => {
        const a = anchorRef.current;
        const t = tipRef.current;
        if (!a || !t) return;
        const r = a.getBoundingClientRect();
        const tw = t.offsetWidth;
        const th = t.offsetHeight;
        const gap = 8;
        const pad = 8;
        let s = side;
        if (s === "top" && r.top - th - gap < pad) s = "bottom";
        else if (s === "bottom" && r.bottom + th + gap > window.innerHeight - pad) s = "top";
        const top = s === "top" ? r.top - th - gap : r.bottom + gap;
        const center = r.left + r.width / 2;
        const left = Math.max(pad, Math.min(center - tw / 2, window.innerWidth - tw - pad));
        setPos({ top, left, arrow: Math.max(8, Math.min(center - left, tw - 8)), side: s });
      };
      place();
      window.addEventListener("scroll", place, true);
      window.addEventListener("resize", place);
      return () => {
        window.removeEventListener("scroll", place, true);
        window.removeEventListener("resize", place);
      };
    }, [open, side]);
    const trigger = React3.cloneElement(children, {
      "aria-describedby": open && describe ? id : children.props["aria-describedby"]
    });
    const bubble = open ? /* @__PURE__ */ React3.createElement(
      "div",
      {
        ref: tipRef,
        id,
        role: "tooltip",
        className: cx("em-tooltip", "text-caption", pos && "em-tooltip--" + pos.side),
        style: pos ? { top: pos.top, left: pos.left, "--em-arrow-x": pos.arrow + "px" } : { visibility: "hidden", top: 0, left: 0 },
        onPointerEnter: () => clear(),
        onPointerLeave: () => openProp === void 0 && hide(),
        ...supportsPopover ? { popover: "manual" } : {}
      },
      content
    ) : null;
    return /* @__PURE__ */ React3.createElement(
      "span",
      {
        ref: anchorRef,
        className: "em-tooltip-anchor",
        onPointerEnter: () => openProp === void 0 && show(false),
        onPointerLeave: () => openProp === void 0 && hide(),
        onFocus: () => openProp === void 0 && show(true),
        onBlur: () => openProp === void 0 && hide()
      },
      trigger,
      bubble && typeof document !== "undefined" ? ReactDOM.createPortal(bubble, document.body) : null
    );
  }
  function Alert({ tone = "info", title, children, action, onClose, closeLabel = "Dismiss", className, ...rest }) {
    return /* @__PURE__ */ React3.createElement("div", { role: tone === "danger" || tone === "warning" ? "alert" : "status", className: cx("em-alert", "em-alert--" + tone, className), ...rest }, /* @__PURE__ */ React3.createElement(Icon, { name: TONE_ICON[tone], size: 20, className: "em-alert__icon" }), /* @__PURE__ */ React3.createElement("div", { className: "em-alert__content" }, /* @__PURE__ */ React3.createElement("p", { className: "em-alert__title text-label" }, title), children != null && /* @__PURE__ */ React3.createElement("div", { className: "em-alert__body text-body" }, children), action && /* @__PURE__ */ React3.createElement("div", { className: "em-alert__action" }, action)), onClose && /* @__PURE__ */ React3.createElement(CloseButton, { label: closeLabel, onClick: onClose, className: "em-alert__close" }));
  }
  function Toast({ tone = "neutral", icon, title, description, action, onClose, duration, closeLabel = "Dismiss", className }) {
    const [paused, setPaused] = React3.useState(false);
    React3.useEffect(() => {
      if (!duration || !onClose || paused) return;
      const t = window.setTimeout(onClose, duration);
      return () => window.clearTimeout(t);
    }, [duration, onClose, paused]);
    const iconName = icon || (tone === "neutral" ? void 0 : TONE_ICON[tone]);
    return /* @__PURE__ */ React3.createElement(
      "div",
      {
        className: cx("em-toast", "em-toast--" + tone, className),
        onPointerEnter: () => setPaused(true),
        onPointerLeave: () => setPaused(false),
        onFocus: () => setPaused(true),
        onBlur: () => setPaused(false)
      },
      iconName && /* @__PURE__ */ React3.createElement(Icon, { name: iconName, size: 20, className: "em-toast__icon" }),
      /* @__PURE__ */ React3.createElement("div", { className: "em-toast__content" }, /* @__PURE__ */ React3.createElement("p", { className: "em-toast__title text-label" }, title), description != null && /* @__PURE__ */ React3.createElement("p", { className: "em-toast__desc text-caption" }, description)),
      action && /* @__PURE__ */ React3.createElement("div", { className: "em-toast__action" }, action),
      onClose && /* @__PURE__ */ React3.createElement(CloseButton, { label: closeLabel, onClick: onClose, className: "em-toast__close" })
    );
  }
  function ToastRegion({ children, placement = "bottom-end", inline = false, label = "Notifications" }) {
    const ref = React3.useRef(null);
    useTopLayer(ref, !inline);
    return /* @__PURE__ */ React3.createElement(
      "section",
      {
        ref,
        "aria-label": label,
        className: cx("em-toast-region", "em-toast-region--" + placement, inline && "em-toast-region--inline"),
        ...!inline && supportsPopover ? { popover: "manual" } : {}
      },
      /* @__PURE__ */ React3.createElement("div", { className: "em-toast-region__list", "aria-live": "polite", "aria-relevant": "additions" }, children)
    );
  }
  function EmptyState({ icon, title, description, actions, size = "md", className }) {
    const Heading = size === "lg" ? "h2" : "h3";
    return /* @__PURE__ */ React3.createElement("div", { className: cx("em-empty", "em-empty--" + size, className) }, icon && /* @__PURE__ */ React3.createElement("span", { className: "em-empty__icon" }, /* @__PURE__ */ React3.createElement(Icon, { name: icon, size: 24 })), /* @__PURE__ */ React3.createElement(Heading, { className: cx("em-empty__title", size === "lg" ? "text-display" : "text-title") }, title), description != null && /* @__PURE__ */ React3.createElement("p", { className: "em-empty__desc text-body-lg" }, description), actions && /* @__PURE__ */ React3.createElement("div", { className: "em-empty__actions" }, actions));
  }

  // ../out/project/components/src/actions.tsx
  var Button = React4.forwardRef(function Button2({ variant = "secondary", size = "md", icon, iconRight, loading = false, fullWidth = false, className, children, disabled, type = "button", ...rest }, ref) {
    return /* @__PURE__ */ React4.createElement(
      "button",
      {
        ref,
        type,
        className: cx(
          "em-btn",
          "em-btn--" + variant,
          "em-btn--" + size,
          size === "lg" ? "text-subtitle" : "text-label",
          fullWidth && "em-btn--block",
          loading && "is-loading",
          className
        ),
        disabled: disabled || loading,
        "aria-busy": loading || void 0,
        ...rest
      },
      loading ? /* @__PURE__ */ React4.createElement(Spinner, { size: 16, tone: "current" }) : icon ? /* @__PURE__ */ React4.createElement(Icon, { name: icon, size: 16 }) : null,
      children != null && /* @__PURE__ */ React4.createElement("span", { className: "em-btn__label" }, children),
      iconRight && !loading && /* @__PURE__ */ React4.createElement(Icon, { name: iconRight, size: 16 })
    );
  });
  var IconButton = React4.forwardRef(function IconButton2({ icon, label, variant = "ghost", size = "md", tooltip = true, className, type = "button", ...rest }, ref) {
    const button = /* @__PURE__ */ React4.createElement(
      "button",
      {
        ref,
        type,
        "aria-label": label,
        className: cx("em-btn", "em-btn--" + variant, "em-btn--" + size, "em-btn--icon", className),
        ...rest
      },
      /* @__PURE__ */ React4.createElement(Icon, { name: icon, size: size === "lg" ? 20 : 16 })
    );
    return tooltip ? /* @__PURE__ */ React4.createElement(Tooltip, { content: label, describe: false }, button) : button;
  });
  function SegmentedControl({ options, value, defaultValue, onChange, label, size = "md", fullWidth = false, className }) {
    const first = options.find((o) => !o.disabled);
    const [current, setCurrent] = useControllable(value, defaultValue !== void 0 ? defaultValue : first ? first.value : "", onChange);
    const refs = React4.useRef([]);
    const onKeyDown = (e, i) => {
      const n = nextIndex(e.key, i, options.length, (k) => !!options[k].disabled, "both");
      if (n < 0) return;
      e.preventDefault();
      setCurrent(options[n].value);
      const el = refs.current[n];
      if (el) el.focus();
    };
    return /* @__PURE__ */ React4.createElement("div", { role: "radiogroup", "aria-label": label, className: cx("em-seg", "em-seg--" + size, fullWidth && "em-seg--block", className) }, options.map((o, i) => {
      const checked = o.value === current;
      return /* @__PURE__ */ React4.createElement(
        "button",
        {
          key: o.value,
          ref: (el) => {
            refs.current[i] = el;
          },
          type: "button",
          role: "radio",
          "aria-checked": checked,
          tabIndex: checked ? 0 : -1,
          disabled: o.disabled,
          className: cx("em-seg__item", "text-label", checked && "is-checked"),
          onClick: () => setCurrent(o.value),
          onKeyDown: (e) => onKeyDown(e, i)
        },
        o.icon && /* @__PURE__ */ React4.createElement(Icon, { name: o.icon, size: 16 }),
        o.label
      );
    }));
  }

  // ../out/project/components/src/forms.tsx
  var React5 = __toESM(require_react());
  function FieldChrome({ id, label, hideLabel, optional, hint, error, className, children }) {
    return /* @__PURE__ */ React5.createElement("div", { className: cx("em-field", className) }, /* @__PURE__ */ React5.createElement("label", { htmlFor: id, className: cx("em-field__label", "text-label", hideLabel && "em-sr-only") }, label, optional && /* @__PURE__ */ React5.createElement("span", { className: "em-field__optional text-caption" }, "Optional")), children, hint != null && !error && /* @__PURE__ */ React5.createElement("p", { id: id + "-hint", className: "em-field__hint text-caption" }, hint), error != null && error !== false && /* @__PURE__ */ React5.createElement("p", { id: id + "-error", className: "em-field__error text-caption" }, /* @__PURE__ */ React5.createElement(Icon, { name: "alert-circle", size: 14 }), /* @__PURE__ */ React5.createElement("span", null, error)));
  }
  function describedBy(id, hint, error, extra) {
    const ids = [extra, error != null && error !== false ? id + "-error" : hint != null ? id + "-hint" : void 0].filter(Boolean);
    return ids.length ? ids.join(" ") : void 0;
  }
  var TextField = React5.forwardRef(function TextField2({ label, hideLabel, optional, hint, error, icon, suffix, size = "md", mono = false, id: idProp, className, disabled, "aria-describedby": extraDescribedBy, ...rest }, ref) {
    const autoId = React5.useId();
    const id = idProp || autoId;
    const invalid = error != null && error !== false;
    return /* @__PURE__ */ React5.createElement(FieldChrome, { id, label, hideLabel, optional, hint, error, className }, /* @__PURE__ */ React5.createElement("div", { className: cx("em-input", "em-input--" + size, invalid && "is-invalid", disabled && "is-disabled") }, icon && /* @__PURE__ */ React5.createElement(Icon, { name: icon, size: 16, className: "em-input__icon" }), /* @__PURE__ */ React5.createElement(
      "input",
      {
        ref,
        id,
        disabled,
        "aria-invalid": invalid || void 0,
        "aria-describedby": describedBy(id, hint, error, extraDescribedBy),
        className: cx("em-input__control", mono ? "text-code" : "text-body"),
        ...rest
      }
    ), suffix != null && /* @__PURE__ */ React5.createElement("span", { className: "em-input__suffix text-caption" }, suffix)));
  });
  var Select = React5.forwardRef(function Select2({ label, hideLabel, optional, hint, error, options, placeholder, size = "md", id: idProp, className, disabled, children, "aria-describedby": extraDescribedBy, ...rest }, ref) {
    const autoId = React5.useId();
    const id = idProp || autoId;
    const invalid = error != null && error !== false;
    return /* @__PURE__ */ React5.createElement(FieldChrome, { id, label, hideLabel, optional, hint, error, className }, /* @__PURE__ */ React5.createElement("div", { className: cx("em-input", "em-select", "em-input--" + size, invalid && "is-invalid", disabled && "is-disabled") }, /* @__PURE__ */ React5.createElement(
      "select",
      {
        ref,
        id,
        disabled,
        "aria-invalid": invalid || void 0,
        "aria-describedby": describedBy(id, hint, error, extraDescribedBy),
        className: "em-input__control text-body",
        ...rest
      },
      placeholder != null && /* @__PURE__ */ React5.createElement("option", { value: "", disabled: true }, placeholder),
      options ? options.map((o) => /* @__PURE__ */ React5.createElement("option", { key: o.value, value: o.value, disabled: o.disabled }, o.label)) : children
    ), /* @__PURE__ */ React5.createElement(Icon, { name: "chevron-down", size: 16, className: "em-select__chevron" })));
  });
  var Checkbox = React5.forwardRef(function Checkbox2({ label, description, indeterminate = false, className, id: idProp, disabled, ...rest }, ref) {
    const autoId = React5.useId();
    const id = idProp || autoId;
    const inner = React5.useRef(null);
    React5.useEffect(() => {
      if (inner.current) inner.current.indeterminate = indeterminate;
    }, [indeterminate]);
    const setRef = (el) => {
      inner.current = el;
      if (typeof ref === "function") ref(el);
      else if (ref) ref.current = el;
    };
    return /* @__PURE__ */ React5.createElement("div", { className: cx("em-check", disabled && "is-disabled", className) }, /* @__PURE__ */ React5.createElement(
      "input",
      {
        ref: setRef,
        id,
        type: "checkbox",
        disabled,
        "aria-describedby": description != null ? id + "-desc" : void 0,
        className: "em-check__box",
        ...rest
      }
    ), /* @__PURE__ */ React5.createElement("span", { className: "em-check__text" }, /* @__PURE__ */ React5.createElement("label", { htmlFor: id, className: "em-check__label text-body" }, label), description != null && /* @__PURE__ */ React5.createElement("span", { id: id + "-desc", className: "em-check__desc text-caption" }, description)));
  });
  var Switch = React5.forwardRef(function Switch2({ label, description, labelFirst = false, className, id: idProp, disabled, ...rest }, ref) {
    const autoId = React5.useId();
    const id = idProp || autoId;
    return /* @__PURE__ */ React5.createElement("div", { className: cx("em-switch", labelFirst && "em-switch--label-first", disabled && "is-disabled", className) }, /* @__PURE__ */ React5.createElement(
      "input",
      {
        ref,
        id,
        type: "checkbox",
        role: "switch",
        disabled,
        "aria-describedby": description != null ? id + "-desc" : void 0,
        className: "em-switch__track",
        ...rest
      }
    ), /* @__PURE__ */ React5.createElement("span", { className: "em-switch__text" }, /* @__PURE__ */ React5.createElement("label", { htmlFor: id, className: "em-switch__label text-body" }, label), description != null && /* @__PURE__ */ React5.createElement("span", { id: id + "-desc", className: "em-switch__desc text-caption" }, description)));
  });

  // ../out/project/components/src/navigation.tsx
  var React6 = __toESM(require_react());
  function Tabs({ items, value, defaultValue, onChange, label, children, className }) {
    const base = React6.useId();
    const first = items.find((t) => !t.disabled);
    const [current, setCurrent] = useControllable(value, defaultValue !== void 0 ? defaultValue : first ? first.value : "", onChange);
    const refs = React6.useRef([]);
    const onKeyDown = (e, i) => {
      const n = nextIndex(e.key, i, items.length, (k) => !!items[k].disabled, "horizontal");
      if (n < 0) return;
      e.preventDefault();
      setCurrent(items[n].value);
      const el = refs.current[n];
      if (el) el.focus();
    };
    const tabId = (v) => base + "-tab-" + v;
    return /* @__PURE__ */ React6.createElement("div", { className: cx("em-tabs", className) }, /* @__PURE__ */ React6.createElement("div", { role: "tablist", "aria-label": label, className: "em-tabs__list" }, items.map((t, i) => {
      const selected = t.value === current;
      return /* @__PURE__ */ React6.createElement(
        "button",
        {
          key: t.value,
          ref: (el) => {
            refs.current[i] = el;
          },
          id: tabId(t.value),
          type: "button",
          role: "tab",
          "aria-selected": selected,
          "aria-controls": base + "-panel",
          tabIndex: selected ? 0 : -1,
          disabled: t.disabled,
          className: cx("em-tabs__tab", "text-label", selected && "is-selected"),
          onClick: () => setCurrent(t.value),
          onKeyDown: (e) => onKeyDown(e, i)
        },
        t.icon && /* @__PURE__ */ React6.createElement(Icon, { name: t.icon, size: 16 }),
        /* @__PURE__ */ React6.createElement("span", null, t.label),
        t.count != null && /* @__PURE__ */ React6.createElement("span", { className: "em-tabs__count text-caption" }, t.count.toLocaleString("en-US"))
      );
    })), children != null && /* @__PURE__ */ React6.createElement("div", { role: "tabpanel", id: base + "-panel", "aria-labelledby": tabId(current), tabIndex: 0, className: "em-tabs__panel" }, children));
  }
  function SideNav({ sections, active, onSelect, header, footer, label = "Main", className }) {
    return /* @__PURE__ */ React6.createElement("nav", { "aria-label": label, className: cx("em-sidenav", className) }, header && /* @__PURE__ */ React6.createElement("div", { className: "em-sidenav__header" }, header), /* @__PURE__ */ React6.createElement("div", { className: "em-sidenav__sections" }, sections.map((s, si) => /* @__PURE__ */ React6.createElement("div", { key: si, className: "em-sidenav__section" }, s.label && /* @__PURE__ */ React6.createElement("p", { className: "em-sidenav__heading text-eyebrow" }, s.label), /* @__PURE__ */ React6.createElement("ul", { className: "em-sidenav__list" }, s.items.map((it) => {
      const current = it.id === active;
      const inner = /* @__PURE__ */ React6.createElement(React6.Fragment, null, it.icon && /* @__PURE__ */ React6.createElement(Icon, { name: it.icon, size: 20, className: "em-sidenav__icon" }), /* @__PURE__ */ React6.createElement("span", { className: "em-sidenav__label" }, it.label), it.count != null && /* @__PURE__ */ React6.createElement("span", { className: "em-sidenav__count text-caption" }, it.count));
      const cls = cx("em-sidenav__item", "text-label", current && "is-current");
      return /* @__PURE__ */ React6.createElement("li", { key: it.id }, it.href ? /* @__PURE__ */ React6.createElement("a", { href: it.href, className: cls, "aria-current": current ? "page" : void 0, onClick: () => onSelect && onSelect(it.id) }, inner) : /* @__PURE__ */ React6.createElement("button", { type: "button", className: cls, "aria-current": current ? "page" : void 0, onClick: () => onSelect && onSelect(it.id) }, inner));
    }))))), footer && /* @__PURE__ */ React6.createElement("div", { className: "em-sidenav__footer" }, footer));
  }

  // ../out/project/components/src/data.tsx
  var React7 = __toESM(require_react());
  function Card({ title, description, actions, footer, headingLevel = 3, padding = "md", className, children, ...rest }) {
    const H = "h" + headingLevel;
    return /* @__PURE__ */ React7.createElement("section", { className: cx("em-card", padding === "none" && "em-card--flush", className), ...rest }, (title != null || actions) && /* @__PURE__ */ React7.createElement("header", { className: "em-card__header" }, /* @__PURE__ */ React7.createElement("div", { className: "em-card__titles" }, title != null && /* @__PURE__ */ React7.createElement(H, { className: "em-card__title text-subtitle" }, title), description != null && /* @__PURE__ */ React7.createElement("p", { className: "em-card__desc text-caption" }, description)), actions && /* @__PURE__ */ React7.createElement("div", { className: "em-card__actions" }, actions)), children != null && /* @__PURE__ */ React7.createElement("div", { className: "em-card__body" }, children), footer && /* @__PURE__ */ React7.createElement("footer", { className: "em-card__footer" }, footer));
  }
  function Sparkline({ data, labels, format, label }) {
    const [hover, setHover] = React7.useState(null);
    const ref = React7.useRef(null);
    const n = data.length;
    if (n < 2) return null;
    const W = 240;
    const H = 48;
    const padT = 6;
    const padB = 6;
    const min = Math.min.apply(null, data);
    const max = Math.max.apply(null, data);
    const span = max - min || 1;
    const xs = data.map((_, i) => i / (n - 1) * W);
    const ys = data.map((v) => padT + (1 - (v - min) / span) * (H - padT - padB));
    const line = xs.map((x, i) => (i ? "L" : "M") + x.toFixed(2) + " " + ys[i].toFixed(2)).join("");
    const area = line + "L" + W + " " + H + "L0 " + H + "Z";
    const tail = "M" + xs[n - 2].toFixed(2) + " " + ys[n - 2].toFixed(2) + "L" + xs[n - 1].toFixed(2) + " " + ys[n - 1].toFixed(2);
    const pct = (i) => ({ left: xs[i] / W * 100 + "%", top: ys[i] / H * 100 + "%" });
    const pick = (clientX) => {
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const f = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
      setHover(Math.round(f * (n - 1)));
    };
    const onKey = (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const cur = hover == null ? n - 1 : hover;
        setHover(Math.min(n - 1, Math.max(0, cur + (e.key === "ArrowRight" ? 1 : -1))));
      } else if (e.key === "Escape") setHover(null);
    };
    const summary = label + ": " + format(data[0]) + " to " + format(data[n - 1]) + ", low " + format(min) + ", high " + format(max);
    return /* @__PURE__ */ React7.createElement(
      "div",
      {
        ref,
        className: "em-spark",
        role: "img",
        "aria-label": summary,
        tabIndex: 0,
        onPointerMove: (e) => pick(e.clientX),
        onPointerLeave: () => setHover(null),
        onKeyDown: onKey,
        onBlur: () => setHover(null)
      },
      /* @__PURE__ */ React7.createElement("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", "aria-hidden": "true" }, /* @__PURE__ */ React7.createElement("path", { d: area, className: "em-spark__area" }), /* @__PURE__ */ React7.createElement("path", { d: line, className: "em-spark__line" }), /* @__PURE__ */ React7.createElement("path", { d: tail, className: "em-spark__tail" })),
      /* @__PURE__ */ React7.createElement("span", { className: "em-spark__dot is-current", style: pct(n - 1), "aria-hidden": "true" }),
      hover != null && /* @__PURE__ */ React7.createElement(React7.Fragment, null, /* @__PURE__ */ React7.createElement("span", { className: "em-spark__cross", style: { left: pct(hover).left }, "aria-hidden": "true" }), /* @__PURE__ */ React7.createElement("span", { className: cx("em-spark__dot", hover === n - 1 && "is-current"), style: pct(hover), "aria-hidden": "true" }), /* @__PURE__ */ React7.createElement("span", { className: cx("em-spark__tip", "text-caption", hover > (n - 1) / 2 && "is-left"), style: { left: pct(hover).left }, "aria-hidden": "true" }, /* @__PURE__ */ React7.createElement("strong", null, format(data[hover])), labels && labels[hover] ? /* @__PURE__ */ React7.createElement("span", null, " · ", labels[hover]) : null))
    );
  }
  function StatCard({ label, value, unit, delta, deltaLabel, goodWhen = "up", trend, trendLabels, formatValue, className }) {
    const fmt = formatValue || ((v) => v.toLocaleString("en-US"));
    let tone = "neutral";
    let arrow = "minus";
    if (delta != null && delta !== 0) {
      const up = delta > 0;
      arrow = up ? "arrow-up-right" : "arrow-down-right";
      tone = up && goodWhen === "up" || !up && goodWhen === "down" ? "success" : "danger";
    }
    return /* @__PURE__ */ React7.createElement("section", { className: cx("em-card", "em-stat", className) }, /* @__PURE__ */ React7.createElement("h3", { className: "em-stat__label text-label" }, label), /* @__PURE__ */ React7.createElement("p", { className: "em-stat__value" }, /* @__PURE__ */ React7.createElement("span", { className: "text-metric" }, value), unit && /* @__PURE__ */ React7.createElement("span", { className: "em-stat__unit text-subtitle" }, unit)), delta != null && /* @__PURE__ */ React7.createElement("p", { className: "em-stat__delta text-caption" }, /* @__PURE__ */ React7.createElement("span", { className: cx("em-delta", "em-delta--" + tone) }, /* @__PURE__ */ React7.createElement(Icon, { name: arrow, size: 14 }), signedPercent(delta)), deltaLabel && /* @__PURE__ */ React7.createElement("span", { className: "em-stat__period" }, deltaLabel)), trend && trend.length > 1 && /* @__PURE__ */ React7.createElement(Sparkline, { data: trend, labels: trendLabels, format: fmt, label }));
  }
  function Table({
    columns,
    rows,
    rowKey,
    caption,
    density = "comfortable",
    sort,
    defaultSort = null,
    onSortChange,
    empty,
    stickyHeader = false,
    maxHeight,
    className
  }) {
    const [innerSort, setInnerSort] = React7.useState(defaultSort);
    const activeSort = sort !== void 0 ? sort : innerSort;
    const sorted = React7.useMemo(() => {
      if (!activeSort || sort !== void 0) return rows;
      const col = columns.find((c) => c.key === activeSort.key);
      if (!col) return rows;
      const get = col.sortValue || ((r) => r[col.key]);
      const dir = activeSort.direction === "asc" ? 1 : -1;
      return rows.slice().sort((a, b) => {
        const va = get(a);
        const vb = get(b);
        return (va < vb ? -1 : va > vb ? 1 : 0) * dir;
      });
    }, [rows, columns, activeSort, sort]);
    const toggle = (key, numeric) => {
      const next = activeSort && activeSort.key === key ? { key, direction: activeSort.direction === "asc" ? "desc" : "asc" } : { key, direction: numeric ? "desc" : "asc" };
      if (sort === void 0) setInnerSort(next);
      if (onSortChange) onSortChange(next);
    };
    const alignOf = (c) => c.align || (c.numeric ? "end" : "start");
    return /* @__PURE__ */ React7.createElement("div", { className: cx("em-table-wrap", stickyHeader && "is-sticky", className), style: maxHeight != null ? { maxHeight } : void 0 }, /* @__PURE__ */ React7.createElement("table", { className: cx("em-table", "em-table--" + density) }, /* @__PURE__ */ React7.createElement("caption", { className: "em-sr-only" }, caption), /* @__PURE__ */ React7.createElement("thead", null, /* @__PURE__ */ React7.createElement("tr", null, columns.map((c) => {
      const isSorted = activeSort && activeSort.key === c.key;
      const ariaSort = c.sortable ? isSorted ? activeSort.direction === "asc" ? "ascending" : "descending" : "none" : void 0;
      return /* @__PURE__ */ React7.createElement("th", { key: c.key, scope: "col", "aria-sort": ariaSort, className: cx("text-label", "is-" + alignOf(c)), style: c.width != null ? { width: c.width } : void 0 }, c.sortable ? /* @__PURE__ */ React7.createElement("button", { type: "button", className: cx("em-table__sort", isSorted && "is-sorted"), onClick: () => toggle(c.key, c.numeric) }, /* @__PURE__ */ React7.createElement("span", null, c.header), /* @__PURE__ */ React7.createElement(Icon, { name: isSorted ? activeSort.direction === "asc" ? "chevron-up" : "chevron-down" : "chevrons-up-down", size: 14 })) : c.header);
    }))), /* @__PURE__ */ React7.createElement("tbody", null, sorted.length === 0 ? /* @__PURE__ */ React7.createElement("tr", { className: "em-table__empty" }, /* @__PURE__ */ React7.createElement("td", { colSpan: columns.length, className: "text-body" }, empty || "Nothing to show yet.")) : sorted.map((r, i) => /* @__PURE__ */ React7.createElement("tr", { key: rowKey ? rowKey(r, i) : i }, columns.map((c) => /* @__PURE__ */ React7.createElement("td", { key: c.key, className: cx(c.mono ? "text-code" : "text-body", "is-" + alignOf(c), c.numeric && "is-numeric") }, c.render ? c.render(r, i) : r[c.key])))))));
  }
  function Badge({ tone = "neutral", icon, dot = false, live = false, size = "md", className, children, ...rest }) {
    return /* @__PURE__ */ React7.createElement("span", { className: cx("em-badge", "em-badge--" + tone, "em-badge--" + size, "text-caption", className), ...rest }, live ? /* @__PURE__ */ React7.createElement("span", { className: "em-badge__dot is-live", "aria-hidden": "true" }) : dot ? /* @__PURE__ */ React7.createElement("span", { className: "em-badge__dot", "aria-hidden": "true" }) : null, icon && /* @__PURE__ */ React7.createElement(Icon, { name: icon, size: 14 }), children);
  }
  function Avatar({ name, src, size = "md", status, className }) {
    const [failed, setFailed] = React7.useState(false);
    const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
    return /* @__PURE__ */ React7.createElement("span", { className: cx("em-avatar", "em-avatar--" + size, className), role: "img", "aria-label": status ? name + ", " + status : name }, src && !failed ? /* @__PURE__ */ React7.createElement("img", { src, alt: "", onError: () => setFailed(true) }) : /* @__PURE__ */ React7.createElement("span", { className: "em-avatar__initials", "aria-hidden": "true" }, initials), status && /* @__PURE__ */ React7.createElement("span", { className: cx("em-avatar__status", "is-" + status), "aria-hidden": "true" }));
  }
  function Progress({ value, max = 100, label, showValue = true, valueText, tone = "ember", size = "md", className }) {
    const id = React7.useId();
    const indeterminate = value == null;
    const pct = indeterminate ? 0 : Math.max(0, Math.min(100, value / max * 100));
    const text = valueText || (indeterminate ? void 0 : Math.round(pct) + "%");
    return /* @__PURE__ */ React7.createElement("div", { className: cx("em-progress", "em-progress--" + tone, "em-progress--" + size, indeterminate && "is-indeterminate", className) }, (label != null || showValue && text) && /* @__PURE__ */ React7.createElement("div", { className: "em-progress__row" }, label != null && /* @__PURE__ */ React7.createElement("span", { id, className: "em-progress__label text-label" }, label), showValue && text && /* @__PURE__ */ React7.createElement("span", { className: "em-progress__value text-caption" }, text)), /* @__PURE__ */ React7.createElement(
      "div",
      {
        className: "em-progress__track",
        role: "progressbar",
        "aria-labelledby": label != null ? id : void 0,
        "aria-valuemin": 0,
        "aria-valuemax": indeterminate ? void 0 : max,
        "aria-valuenow": indeterminate ? void 0 : value,
        "aria-valuetext": valueText
      },
      /* @__PURE__ */ React7.createElement("span", { className: "em-progress__fill", style: indeterminate ? void 0 : { width: pct + "%" } })
    ));
  }

  // ../out/project/components/src/Dialog.tsx
  var React8 = __toESM(require_react());
  function Dialog({ open, onClose, title, description, children, footer, size = "md", dismissible = true, inline = false, closeLabel = "Close", className }) {
    const ref = React8.useRef(null);
    const base = React8.useId();
    React8.useEffect(() => {
      const el = ref.current;
      if (!el || inline) return;
      if (open && !el.open) el.showModal();
      else if (!open && el.open) el.close();
    }, [open, inline]);
    const panel = /* @__PURE__ */ React8.createElement("div", { className: cx("em-dialog__panel", "em-dialog__panel--" + size) }, /* @__PURE__ */ React8.createElement("header", { className: "em-dialog__header" }, /* @__PURE__ */ React8.createElement("div", { className: "em-dialog__titles" }, /* @__PURE__ */ React8.createElement("h2", { id: base + "-title", className: "em-dialog__title text-title" }, title), description != null && /* @__PURE__ */ React8.createElement("p", { id: base + "-desc", className: "em-dialog__desc text-body" }, description)), /* @__PURE__ */ React8.createElement(CloseButton, { label: closeLabel, onClick: onClose, className: "em-dialog__close" })), children != null && /* @__PURE__ */ React8.createElement("div", { className: "em-dialog__body" }, children), footer && /* @__PURE__ */ React8.createElement("footer", { className: "em-dialog__footer" }, footer));
    if (inline) {
      return open ? /* @__PURE__ */ React8.createElement("div", { className: cx("em-dialog-inline", className), role: "dialog", "aria-modal": "false", "aria-labelledby": base + "-title", "aria-describedby": description != null ? base + "-desc" : void 0 }, panel) : null;
    }
    return /* @__PURE__ */ React8.createElement(
      "dialog",
      {
        ref,
        className: cx("em-dialog", className),
        "aria-labelledby": base + "-title",
        "aria-describedby": description != null ? base + "-desc" : void 0,
        onCancel: (e) => {
          e.preventDefault();
          onClose();
        },
        onClick: (e) => {
          if (dismissible && e.target === ref.current) onClose();
        }
      },
      open ? panel : null
    );
  }

  // <stdin>
  window.Ember = Object.assign(window.Ember || {}, src_exports);
})();

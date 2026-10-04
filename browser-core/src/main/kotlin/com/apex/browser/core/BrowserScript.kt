package com.apex.browser.core

/**
 * 注入到 WebView 的 JS 片段（纯字符串，无 Android 依赖）。
 *
 * [SNAPSHOT_JS] 遍历可见可交互元素并序列化回传，[HIGHLIGHT_JS] 在调试时高亮某个 ref。
 * 返回格式与 [RawDomElement] 对齐，由 [DomParser] 解析。
 *
 * 2026 裁决要点：
 * - 稳定 ref 改为「语义哈希」`r_<hash>`（基于 role+text+tag+相对位置），写入 `data-apex-hash`，
 *   抗 SPA 局部刷新错位（替代旧的顺序/属性混合 ref `data-apex-ref`）。
 * - JS 层启发式剪枝：跳过不可见、非交互无文本、零尺寸节点；硬上限 [SNAPSHOT_MAX_ELEMENTS]。
 * - 返回的每个元素都带 `data-apex-hash`，供后续 DOM 级定位与物理触摸注入使用。
 */
object BrowserScript {

    /** 单次快照元素硬上限（Token 预算保护，超过即截断） */
    const val SNAPSHOT_MAX_ELEMENTS: Int = 50

    /**
     * 抓取当前**视口内**可见可交互元素，返回 JSON 数组字符串（需用 JSON.parse 还原）。
     * 与早期全量快照不同，本脚本在 JS 层完成过滤 + 语义哈希，极大减少 IPC 体积。
     *
     * @param strategy 剪枝策略（#19/#20）：传入 [DomParser.SnapshotStrategy]，
     *   不同策略收窄查询选择器，进一步降低回传体积。
     */
    fun snapshotJs(strategy: DomParser.SnapshotStrategy = DomParser.SnapshotStrategy.INTERACTIVE_ONLY): String {
        val sel = when (strategy) {
            DomParser.SnapshotStrategy.INTERACTIVE_ONLY ->
                "a,button,input,select,textarea,summary,area[href]," +
                "[role=button],[role=link],[role=tab],[role=option],[role=checkbox],[role=radio],[role=switch]"
            DomParser.SnapshotStrategy.FORM_FIELDS ->
                "input,select,textarea,[role=checkbox],[role=radio],[role=switch],[role=textbox],[role=searchbox]"
            DomParser.SnapshotStrategy.CONTENT_SUMMARY ->
                "a[href],h1,h2,h3,h4,p,li,[role=heading],[role=link]"
        }
        return """
        (function(){
          function hash(str){
            var h = 0;
            for (var i=0;i<str.length;i++){ var c = str.charCodeAt(i); h = ((h<<5)-h)+c; h|=0; }
            return 'r_' + (Math.abs(h)>>>0).toString(36);
          }
          var MAX = $SNAPSHOT_MAX_ELEMENTS;
          var out = [];
          var interactiveSel = ${"'$sel'"};
          var all = document.querySelectorAll(interactiveSel);
          for (var i=0;i<all.length;i++){
            if (out.length >= MAX) break;
            var el = all[i];
            var style = window.getComputedStyle(el);
            // 启发式剪枝：不可见 / 透明 / 脱离布局 / 零尺寸
            var visible = style.display !== 'none' && style.visibility !== 'hidden'
                  && parseFloat(style.opacity) > 0.05 && el.offsetParent !== null;
            if (!visible) continue;
            var rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            var text = (el.innerText || el.value || el.placeholder || '').replace(/\s+/g,' ').trim();
            // 非交互且无文本 -> 纯布局噪音，跳过
            if (text.length === 0 && !el.hasAttribute('aria-label') && !el.hasAttribute('placeholder')
                && el.tagName !== 'INPUT' && el.tagName !== 'SELECT' && el.tagName !== 'TEXTAREA') continue;
            text = text.slice(0, 120);
            // 语义哈希 ref：role + 文本 + 标签 + 相对顶部位置（抗 SPA 局部刷新错位）
            var role = el.getAttribute('role') || el.tagName.toLowerCase();
            var semanticKey = role + '|' + text + '|' + el.tagName + '|' + Math.round(rect.top + window.scrollY);
            var ref = hash(semanticKey);
            el.setAttribute('data-apex-hash', ref);
            var attrs = { 'data-apex-hash': ref };
            var keep = ['href','name','type','placeholder','value','aria-label','title','role','alt','id'];
            for (var a=0;a<el.attributes.length;a++){
              var an = el.attributes[a].name; if (keep.indexOf(an)>=0) attrs[an] = el.attributes[a].value;
            }
            out.push({
              tag: el.tagName,
              text: text,
              attributes: attrs,
              rect: { x: Math.round(rect.left + window.scrollX), y: Math.round(rect.top + window.scrollY),
                      width: Math.round(rect.width), height: Math.round(rect.height) },
              isVisible: true,
              isInteractive: true,
              depth: 0,
              childCount: el.children.length
            });
          }
          return JSON.stringify(out);
        })();
        """.trimIndent()
    }

    /**
     * A11y 补充源（#17）：当主快照交互元素过少（CSP 阻止 JS 或页面极简）时，
     * 用 ARIA 角色补充语义元素（含有 accessible name 的容器），作为 DOM 快照的降级源。
     */
    val A11Y_FALLBACK_JS: String
        get() = """
        (function(){
          function hash(str){
            var h = 0;
            for (var i=0;i<str.length;i++){ var c = str.charCodeAt(i); h = ((h<<5)-h)+c; h|=0; }
            return 'r_' + (Math.abs(h)>>>0).toString(36);
          }
          var MAX = $SNAPSHOT_MAX_ELEMENTS;
          var out = [];
          // 有 accessible name 的节点：role / aria-label / 文本 任一即可
          var all = document.querySelectorAll('*');
          for (var i=0;i<all.length && out.length<MAX;i++){
            var el = all[i];
            var role = el.getAttribute && el.getAttribute('role');
            var name = (el.getAttribute && (el.getAttribute('aria-label')||el.getAttribute('title'))) ||
                       (el.innerText || '').replace(/\\s+/g,' ').trim().slice(0,80);
            if (!role && (!name || name.length===0)) continue;
            // 跳过纯布局容器（无语义 role 且无标签）
            if (!role && el.children.length>0 && el.innerText.trim().length>120) continue;
            var rect = el.getBoundingClientRect();
            if (rect.width===0 || rect.height===0) continue;
            var text = (name||'').toString().slice(0,120);
            var semanticKey = (role||el.tagName) + '|' + text + '|' + el.tagName;
            var ref = hash(semanticKey);
            el.setAttribute('data-apex-hash', ref);
            out.push({
              tag: el.tagName,
              text: text,
              attributes: { 'data-apex-hash': ref, 'role': role||'', 'aria-label': (el.getAttribute&&el.getAttribute('aria-label'))||'' },
              rect: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
              isVisible: true,
              isInteractive: !!role,
              depth: 0,
              childCount: el.children.length
            });
          }
          return JSON.stringify(out);
        })();
        """.trimIndent()

    /**
     * 网络监控（#18）：拦截 fetch / XMLHttpRequest，记录 API 请求到 window.__apexNetLog，
     * 供 [browser_network_log] 工具读取。注入一次即可持续生效。
     */
    val NETWORK_MONITOR_JS: String
        get() = """
        (function(){
          if (window.__apexNetHooked) return;
          window.__apexNetLog = window.__apexNetLog || [];
          window.__apexNetHooked = true;
          function rec(method, url, status){
            window.__apexNetLog.push({ method: method, url: (url||'').toString().slice(0,200), status: status||0, t: Date.now() });
            if (window.__apexNetLog.length > 200) window.__apexNetLog.shift();
          }
          var origFetch = window.fetch;
          if (origFetch) window.fetch = function(){
            var args = arguments; var u = args[0];
            return origFetch.apply(this, args).then(function(r){ rec('fetch', u, r.status); return r; }, function(e){ rec('fetch', u, 0); throw e; });
          };
          var origXhr = window.XMLHttpRequest.prototype.open;
          window.XMLHttpRequest.prototype.open = function(m,u){ this.__apexMethod=m; this.__apexUrl=u; return origXhr.apply(this, arguments); };
          var origSend = window.XMLHttpRequest.prototype.send;
          window.XMLHttpRequest.prototype.send = function(){
            var self=this; var mu=this.__apexMethod, ul=this.__apexUrl;
            this.addEventListener('loadend', function(){ rec(mu, ul, self.status); });
            return origSend.apply(this, arguments);
          };
        })();
        """.trimIndent()

    /** 物理触摸注入：返回元素在**屏幕坐标系**中的中心点（含 WebView 自身偏移），失败返回 null */
    fun rectByRefJs(ref: String): String =
        """
        (function(){
          ${refHitsJs("ref.toJsonString()")}
          var el = __apexFirst;
          if (!el) return JSON.stringify(null);
          var r = el.getBoundingClientRect();
          return JSON.stringify({ x: r.left + r.width/2, y: r.top + r.height/2,
                                  left: r.left, top: r.top, width: r.width, height: r.height });
        })();
        """.trimIndent()

    /** 点击后验证：对比点击前后页面状态（URL / 标题 / 可交互元素数量） */
    val POST_ACTION_PROBE_JS: String
        get() = """
        (function(){
          return JSON.stringify({
            url: location.href,
            title: document.title,
            interactiveCount: document.querySelectorAll('a,button,input,select,textarea,[role=button],[role=link],[role=tab],[role=option],[role=checkbox],[role=radio],[role=switch]').length,
            scrollY: window.scrollY
          });
        })();
        """.trimIndent()

    /** 等待目标 CSS 选择器出现（用于 navigate 的 wait_for 参数），超时由 Kotlin 层控制 */
    fun waitForSelectorJs(selector: String): String =
        """
        (function(){
          return new Promise(function(resolve){
            var el = document.querySelector(${selector.toJsonString()});
            if (el) return resolve(true);
            var t = setInterval(function(){
              var e = document.querySelector(${selector.toJsonString()});
              if (e){ clearInterval(t); resolve(true); }
            }, 200);
            setTimeout(function(){ clearInterval(t); resolve(false); }, 10000);
          });
        })();
        """.trimIndent()

    /** 下拉选择：按 value 或可见文本设置 <select> 并触发 change */
    fun selectJs(ref: String, value: String, byText: Boolean): String {
        val match = if (byText) "opt.text" else "opt.value"
        return """
        (function(){
          ${refHitsJs("ref.toJsonString()")}
          var el = __apexFirst;
          if (!el || el.tagName !== 'SELECT') return false;
          var opts = el.options;
          for (var i=0;i<opts.length;i++){
            var opt = opts[i];
            if (($match) === ${value.toJsonString()}){ el.selectedIndex = i; el.dispatchEvent(new Event('change',{bubbles:true})); return true; }
          }
          return false;
        })();
        """
    }

    /** 高亮指定 ref 对应的元素（调试 / 可视化；v1.1.0：ref 走统一转义） */
    fun highlightJs(ref: String, color: String = "#1e90ff"): String =
        """
        (function(){
          ${refHitsJs("ref.toJsonString()")}
          var els = __apexHits;
          for (var i=0;i<els.length;i++){ els[i].style.outline='2px solid $color'; }
        })();
        """.trimIndent()

    // ═══════════════════════════════════════════════════════════════════
    //  v1.1.0 高级能力脚本（错误率削减 + 动作空间扩展）
    // ═══════════════════════════════════════════════════════════════════

    /**
     * 滚动元素到视口中央并回读其**视口坐标**矩形（v1.1.0 点击前置步骤）。
     *
     * 旧 clickElement 直接拿 rect 中心注入触摸：元素在折叠线以下时中心点
     * 落在 WebView 边界外 → dispatchTouchEvent 打空 / 误中别的元素。本脚本
     * 先 scrollIntoView({block:'center'})，再返回矩形与 found 标记。
     */
    fun scrollIntoViewAndRectJs(ref: String): String =
        """
        (function(){
          ${refHitsJs("ref.toJsonString()")}
          var el = __apexFirst;
          if (!el) return JSON.stringify(null);
          el.scrollIntoView({block:'center', inline:'nearest'});
          var r = el.getBoundingClientRect();
          return JSON.stringify({ found: true,
                                  x: r.left + r.width/2, y: r.top + r.height/2,
                                  left: r.left, top: r.top, width: r.width, height: r.height,
                                  vw: window.innerWidth, vh: window.innerHeight });
        })();
        """.trimIndent()

    /**
     * 命中测试：视口坐标 (x,y) 处最顶层元素是谁（v1.1.0 遮挡检测）。
     *
     * 返回该元素的 data-apex-hash 与 tag（无 hash 则打上临时标记），
     * 以及它是否为目标 ref 元素自身或其后代。粘性顶栏 / 模态遮罩
     * 拦截触摸时，据此给出「被遮挡」的明确失败原因而不是静默误点。
     */
    fun elementAtPointJs(x: Float, y: Float, targetRef: String): String =
        """
        (function(){
          var el = document.elementFromPoint($x, $y);
          if (!el) return JSON.stringify({ hit: false });
          ${refHitsJs("targetRef.toJsonString()")}
          var target = __apexFirst;
          var isTargetOrChild = !!(target && (el === target || target.contains(el)));
          var h = el.getAttribute('data-apex-hash');
          if (!h) { h = 'hit_' + Math.abs((el.tagName + '|' + (el.innerText||'').slice(0,40)).split('').reduce(function(a,c){return ((a<<5)-a)+c.charCodeAt(0)|0;},0)).toString(36); el.setAttribute('data-apex-hash', h); }
          return JSON.stringify({ hit: true, hash: h, tag: el.tagName, isTargetOrChild: isTargetOrChild });
        })();
        """.trimIndent()

    /**
     * 模糊重定位（v1.1.0 错误率核心削减件）。
     *
     * SPA 局部刷新 / 文案微调会让语义哈希失配——旧实现直接抛
     * ElementNotFoundException 终止整个动作。本脚本按「tag + 文本包含」
     * 做包含式匹配，命中后**重新打上请求的 data-apex-hash** 并回读矩形，
     * 使后续动作可以无缝续跑（自愈而非报错）。
     */
    fun locateByFuzzyJs(tag: String, textContains: String, newRef: String): String =
        """
        (function(){
          var want = ${textContains.toJsonString()};
          var tag = ${tag.toJsonString()};
          var nodes = document.querySelectorAll(tag);
          var best = null, bestLen = Infinity;
          for (var i=0;i<nodes.length;i++){
            var el = nodes[i];
            var t = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').replace(/\s+/g,' ').trim();
            if (!t || t.indexOf(want) < 0) continue;
            var style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            var r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            // 最短文本 = 最精确匹配（避免命中包含目标文案的大容器）
            if (t.length < bestLen) { best = el; bestLen = t.length; }
          }
          if (!best) return JSON.stringify(null);
          best.setAttribute('data-apex-hash', ${newRef.toJsonString()});
          var r = best.getBoundingClientRect();
          return JSON.stringify({ ref: ${newRef.toJsonString()}, tag: best.tagName,
                                  text: (best.innerText||'').replace(/\s+/g,' ').trim().slice(0,120),
                                  x: r.left + r.width/2, y: r.top + r.height/2,
                                  left: r.left, top: r.top, width: r.width, height: r.height });
        })();
        """.trimIndent()

    /**
     * React/Vue 安全写值（v1.1.0 替代旧 `el.value = x` 直赋）。
     *
     * 受控组件框架用原生 value 描述符拦截直赋——`el.value = x` 后框架
     * 的内部 state 不更新，下一次渲染把值弹回原样（Agent 看到「输入成功」
     * 实际没输进去）。本脚本：
     * 1. 输入类元素走原型链 native setter；
     * 2. contenteditable 走 textContent / execCommand 兜底；
     * 3. 派发 input + change 事件（bubbles）；
     * 4. append 模式在现值后追加而非替换。
     * 换行文本经 Kotlin 层转义为 \n 字面量，多行输入不再撕裂 JS 字符串。
     */
    fun setNativeValueJs(ref: String, text: String, append: Boolean): String =
        """
        (function(){
          ${refHitsJs("ref.toJsonString()")}
          var el = __apexFirst;
          if (!el) return JSON.stringify({ ok: false, reason: 'not_found' });
          el.focus();
          var ok = true, reason = '';
          if (el.isContentEditable) {
            if (document.execCommand) { document.execCommand('selectAll', false, null); document.execCommand('insertText', false, ${text.toJsonString()}); }
            else el.textContent = ${text.toJsonString()};
            el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:${text.toJsonString()}}));
          } else if (typeof el.value === 'string' || el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
            var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            var desc = Object.getOwnPropertyDescriptor(proto, 'value');
            var cur = desc && desc.get ? desc.get.call(el) : el.value;
            var next = ${if (append) "(cur || '') + ${text.toJsonString()}" else text.toJsonString()};
            if (desc && desc.set) desc.set.call(el, next); else el.value = next;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
          } else { ok = false; reason = 'not_editable'; }
          return JSON.stringify({ ok: ok, reason: reason, value: (el.value !== undefined ? String(el.value).slice(0,80) : (el.textContent||'').slice(0,80)) });
        })();
        """.trimIndent()

    /** 键名 → {code, keyCode} 映射表（v1.1.0 pressKey 用，与 UI Events 规范对齐）。 */
    private val KEY_MAP: Map<String, Pair<String, Int>> = mapOf(
        "enter" to ("Enter" to 13), "tab" to ("Tab" to 9), "escape" to ("Escape" to 27),
        "backspace" to ("Backspace" to 8), "delete" to ("Delete" to 46),
        "arrowup" to ("ArrowUp" to 38), "arrowdown" to ("ArrowDown" to 40),
        "arrowleft" to ("ArrowLeft" to 37), "arrowright" to ("ArrowRight" to 39),
        "pageup" to ("PageUp" to 33), "pagedown" to ("PageDown" to 34),
        "home" to ("Home" to 36), "end" to ("End" to 35),
        "space" to (" " to 32),
    )

    /**
     * 键盘事件注入（v1.1.0）：对 activeElement 依次派发 keydown → keypress → keyup。
     *
     * Enter 的表单语义单独处理：未被 preventDefault 且目标在 form 内时调用
     * `form.requestSubmit()`——纯 KeyboardEvent 不会触发浏览器隐式提交，
     * 「输入搜索词后按回车没反应」是旧链路的高频失败场景。
     */
    fun pressKeyJs(key: String): String {
        val norm = key.trim().lowercase()
        val (jsKey, keyCode) = KEY_MAP[norm] ?: (key.take(1) to key.codePointAt(0))
        val code = if (jsKey.length == 1) jsKey.uppercase().let { "Key$it" } else jsKey
        return """
        (function(){
          var target = document.activeElement || document.body;
          var key = ${jsKey.toJsonString()}, code = ${code.toJsonString()}, kc = $keyCode;
          function fire(type, ctor){ var e; try { e = new ctor(type, {key:key, code:code, keyCode:kc, which:kc, bubbles:true, cancelable:true}); } catch(err){ e = document.createEvent('KeyboardEvent'); e.initKeyboardEvent(type, true, true, null, key, kc); } target.dispatchEvent(e); return e; }
          var kd = fire('keydown', KeyboardEvent);
          if (!kd.defaultPrevented) {
            fire('keypress', KeyboardEvent);
            if (key === 'Enter' && target.form && typeof target.form.requestSubmit === 'function') {
              try { target.form.requestSubmit(); } catch(e){}
            }
          }
          fire('keyup', KeyboardEvent);
          return true;
        })();
        """.trimIndent()
    }

    /** 悬停事件注入（v1.1.0）：mouseover → mouseenter → mousemove，驱动 CSS :hover 与悬停菜单。 */
    fun hoverJs(ref: String): String =
        """
        (function(){
          ${refHitsJs("ref.toJsonString()")}
          var el = __apexFirst;
          if (!el) return false;
          var r = el.getBoundingClientRect();
          var opts = { bubbles: true, cancelable: true, clientX: r.left + r.width/2, clientY: r.top + r.height/2 };
          el.dispatchEvent(new MouseEvent('mouseover', opts));
          el.dispatchEvent(new MouseEvent('mouseenter', Object.assign({}, opts, {bubbles: false})));
          el.dispatchEvent(new MouseEvent('mousemove', opts));
          return true;
        })();
        """.trimIndent()

    /**
     * 结构化内容抽取（v1.1.0）：article / tables / links / meta 四模式。
     *
     * - article：readability-lite——优先 article/main/[role=main]，否则取文本
     *   最长的 div/section，剔除 script/style/nav/footer/aside 后回传正文；
     * - tables：全部 <table> 的表头 + 行（50 行 × 30 列封顶）；
     * - links：去重后的 a[href]（文本 + 链接，200 条封顶）；
     * - meta：title / description / canonical / og 标签。
     */
    fun extractContentJs(mode: String): String {
        val m = when (mode) {
            ExtractMode.TABLES.value -> "tables"
            ExtractMode.LINKS.value -> "links"
            ExtractMode.META.value -> "meta"
            else -> "article"
        }
        return """
        (function(){
          var mode = ${m.toJsonString()};
          function txt(el, cap){ return (el && (el.innerText || el.textContent) || '').replace(/\s+/g,' ').trim().slice(0, cap || 20000); }
          if (mode === 'meta') {
            function meta(n){ var el = document.querySelector('meta[property="'+n+'"], meta[name="'+n+'"]'); return el ? (el.getAttribute('content')||'') : ''; }
            var can = document.querySelector('link[rel=canonical]');
            return JSON.stringify({ title: document.title, description: meta('description'),
              canonical: can ? can.href : '', ogTitle: meta('og:title'), ogDescription: meta('og:description'), ogImage: meta('og:image'), url: location.href });
          }
          if (mode === 'links') {
            var seen = {}, out = [], as = document.querySelectorAll('a[href]');
            for (var i=0;i<as.length && out.length<200;i++){
              var a = as[i], t = txt(a, 120), h = a.href;
              if (!h || seen[h]) continue; seen[h] = 1;
              out.push({ text: t, href: h });
            }
            return JSON.stringify(out);
          }
          if (mode === 'tables') {
            var tabs = document.querySelectorAll('table'), out2 = [];
            for (var i=0;i<tabs.length && out2.length<20;i++){
              var tb = tabs[i], rows = [];
              var trs = tb.querySelectorAll('tr');
              for (var j=0;j<trs.length && rows.length<50;j++){
                var cells = [], tds = trs[j].querySelectorAll('th,td');
                for (var k=0;k<tds.length && cells.length<30;k++) cells.push(txt(tds[k], 200));
                if (cells.length) rows.push(cells);
              }
              if (rows.length) out2.push({ caption: txt(tb.caption, 120), rows: rows });
            }
            return JSON.stringify(out2);
          }
          // article 模式
          var root = document.querySelector('article') || document.querySelector('main') || document.querySelector('[role=main]');
          if (!root) {
            var best = null, bestLen = 0, divs = document.querySelectorAll('div,section');
            for (var i=0;i<divs.length;i++){
              var d = divs[i], l = (d.innerText||'').length;
              if (l > bestLen) { best = d; bestLen = l; }
            }
            root = best || document.body;
          }
          var clone = root.cloneNode(true);
          var junk = clone.querySelectorAll('script,style,nav,footer,aside,header,noscript,[aria-hidden=true]');
          for (var i=0;i<junk.length;i++) junk[i].remove();
          var text = txt(clone, 20000);
          return JSON.stringify({ title: document.title, url: location.href, wordCount: text.length, text: text });
        })();
        """.trimIndent()
    }

    /** 当前 URL 读取（v1.1.0 waitForUrl 轮询用）。 */
    val CURRENT_URL_JS: String
        get() = "(function(){ return location.href; })();"

    /** 内容抽取模式。 */
    enum class ExtractMode(val value: String) {
        ARTICLE("article"), TABLES("tables"), LINKS("links"), META("meta")
    }
}

/**
 * 生成「按语义哈希 ref 定位元素」的 JS 前置片段：声明 `__apexHits`（全部匹配元素）。
 *
 * ## 为什么不能把 ref 拼进 CSS 属性选择器
 *
 * 旧形态 `querySelector('[data-apex-hash=${ref.toJsonString()}]')` 有两个同时存在的故障：
 *  1. **双重引号** —— 模板自带单引号，而转义函数又套一层单引号，两者不匹配直接报语法错误。
 *     生成 `document.querySelector('[data-apex-hash='r_3k9f']')` —— 对**任何** ref 都是语法错误，
 *     因此 click / input / select 完全无法工作。
 *  2. **可注入** —— 由于转义已正确跳过引号，但注入载荷**不需要任何引号字符**即可脱离：
 *     `ref = "+alert(document.cookie)+"` 生成
 *     `document.querySelector('[data-apex-hash='+alert(document.cookie)+']')`
 *     —— 合法 JS，页内任意代码执行。
 *
 * 改为「取全部带标记元素 → 在 JS 内用 === 严格比较属性值」后，ref 全程只是一个 JS
 * 字符串，**不经过 CSS 解析**：无注入面，也不会因畸形 ref 抛错。
 */
private fun refHitsJs(refExpr: String): String =
    """
        var __apexHits = [];
        var __apexMarked = document.querySelectorAll('[data-apex-hash]');
        for (var __apexI = 0; __apexI < __apexMarked.length; __apexI++) {
          if (__apexMarked[__apexI].getAttribute('data-apex-hash') === ${refExpr}) { __apexHits.push(__apexMarked[__apexI]); }
        }
        var __apexFirst = __apexHits.length ? __apexHits[0] : null;
    """.trimIndent()

/** 把字符串安全包成 JS 单引号字面量（v1.1.0 修复：此前零转义）
 *
 * 旧实现 `"'$this'"` 不做任何转义：ref / selector / value 含单引号、换行或
 * 反斜杠时生成的 JS 直接语法报错——CSS 属性选择器 `[href='foo']` 是 Agent
 * 常用形态， waitForSelectorJs 一直在这种输入下静默失败（evaluateJavascript
 * 返回 null → 上层报「超时」而非真实原因）。
 * 现在转义：反斜杠、单/双引号、换行、回车、行/段分隔符、</ 序列。
 */
private fun String.toJsonString(): String {
    // 预处理：防 </script> 提前闭合（WebView 内联脚本上下文的防御性转义）
    val src = this.replace("</", "<\\/")
    return buildString {
        append('\'')
        for (c in src) {
            when (c) {
                '\\' -> append("\\\\")
                '\'' -> append("\\'")
                '"' -> append("\\\"")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\u2028' -> append("\\u2028")
                '\u2029' -> append("\\u2029")
                else -> append(c)
            }
        }
        append('\'')
    }
}

package com.apex.browser.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 验证 BrowserScript 的「意图」：它生成的 JS 字符串决定了 WebView 抓取的质量与定位稳定性。
 * 这些是纯字符串工厂（无 Android 依赖），在 JVM 即可验证。
 * 意图层失败（而非实现层）即视为 bug —— 例如有人把语义哈希 ref 改回顺序 ref、
 * 弄错策略选择器、或在 ref 插值处留下注入漏洞，都应被本测试捕获。
 */
class BrowserScriptTest {

    /**
     * 从 snapshotJs 生成的脚本中提取实际的选择器条目列表。
     *
     * 修复说明：此前用 `js.contains("a,")` 做子串匹配，会被 `textarea,` 的
     * 子串 `a,` 误命中（假阳性），“不应含裸 <a> 选择器”的断言因此永远失败；
     * 反过来 INTERACTIVE_ONLY 的断言即使删掉真正的 `a` 选择器也会因
     * `textarea,` 而侥幸通过（假阴性）。解析出真实条目才能验证意图。
     */
    private fun selectorEntries(js: String): List<String> {
        val m = Regex("var interactiveSel = '([^']*)'").find(js)
        return m?.groupValues?.get(1)?.split(',')?.map { it.trim() } ?: emptyList()
    }

    // ---- snapshotJs：三策略选择器正确性 ----

    @Test
    fun `INTERACTIVE_ONLY 策略覆盖核心可交互控件选择器`() {
        val entries = selectorEntries(BrowserScript.snapshotJs(DomParser.SnapshotStrategy.INTERACTIVE_ONLY))
        // 意图：默认快照必须能抓到链接、按钮、表单控件与常见 ARIA 角色
        for (sel in listOf("a", "button", "input", "select", "textarea",
                           "[role=button]", "[role=link]", "[role=tab]", "[role=option]")) {
            assertTrue("INTERACTIVE_ONLY 应含选择器 $sel，实际：$entries", entries.contains(sel))
        }
    }

    @Test
    fun `FORM_FIELDS 策略收窄到表单域而非整页链接`() {
        val entries = selectorEntries(BrowserScript.snapshotJs(DomParser.SnapshotStrategy.FORM_FIELDS))
        // 意图：填表场景只保留 input/select/textarea 及表单类 ARIA 角色
        for (sel in listOf("input", "select", "textarea", "[role=checkbox]")) {
            assertTrue("FORM_FIELDS 应含选择器 $sel，实际：$entries", entries.contains(sel))
        }
        // 不应把整页导航链接/按钮作为主要目标（裸 a / button 不在 FORM_FIELDS 选择器内）
        assertFalse("FORM_FIELDS 不应含裸 <a> 选择器，实际：$entries", entries.contains("a"))
        assertFalse("FORM_FIELDS 不应含裸 <button> 选择器，实际：$entries", entries.contains("button"))
    }

    @Test
    fun `CONTENT_SUMMARY 策略保留标题正文链接而非交互控件`() {
        val entries = selectorEntries(BrowserScript.snapshotJs(DomParser.SnapshotStrategy.CONTENT_SUMMARY))
        // 意图：阅读/抽取场景保留 h1~h4、p、li、链接
        for (sel in listOf("h1", "h2", "h3", "h4", "p", "li", "a[href]")) {
            assertTrue("CONTENT_SUMMARY 应含选择器 $sel，实际：$entries", entries.contains(sel))
        }
        assertFalse("CONTENT_SUMMARY 不应含裸 button 选择器，实际：$entries", entries.contains("button"))
        assertFalse("CONTENT_SUMMARY 不应含裸 input 选择器，实际：$entries", entries.contains("input"))
    }

    // ---- 语义哈希 ref：抗 SPA 局部刷新错位的核心 ----

    @Test
    fun `snapshotJs 注入 data-apex-hash 语义哈希作为定位主键`() {
        val js = BrowserScript.snapshotJs()
        // 意图：定位主键必须是语义哈希（data-apex-hash），而非顺序 ref)
        assertTrue("必须写入 data-apex-hash 属性", js.contains("setAttribute('data-apex-hash'"))
        assertTrue("必须包含哈希函数", js.contains("function hash"))
        assertTrue("哈希结果必须以 r_ 前缀稳定可读", js.contains("'r_' +"))
    }

    @Test
    fun `snapshotJs 含元素硬上限保护 token 预算`() {
        val js = BrowserScript.snapshotJs()
        // 意图：超过 SNAPSHOT_MAX_ELEMENTS 必须截断，防止大页撑爆 IPC/Token
        assertTrue(js.contains("var MAX = ${BrowserScript.SNAPSHOT_MAX_ELEMENTS}"))
        assertTrue(js.contains("if (out.length >= MAX) break"))
    }

    @Test
    fun `snapshotJs 默认策略为 INTERACTIVE_ONLY`() {
        // 意图：不传策略时回退到最通用的交互元素快照，行为稳定
        assertEquals(
            BrowserScript.snapshotJs(DomParser.SnapshotStrategy.INTERACTIVE_ONLY),
            BrowserScript.snapshotJs()
        )
    }

    // ---- 物理定位：rectByRefJs 用语义哈希 ref 而非顺序序号 ----

    @Test
    fun `rectByRefJs 按 data-apex-hash 定位而非顺序 ref`() {
        val ref = "r_3k9f"
        val js = BrowserScript.rectByRefJs(ref)
        // 意图：必须按语义哈希 ref 查询，才能抗 SPA 局部刷新错位
        // 新契约：ref 不再拼进 CSS 选择器，而是在 JS 内与属性值严格比较
        assertTrue(
            "应按 data-apex-hash 属性值定位：" + js,
            js.contains("getAttribute('data-apex-hash') === '$ref'"),
        )
    }

    @Test
    fun `rectByRefJs 把外部 ref 嵌入属性选择器定位`() {
        // 意图：ref 来自 Agent 参数，必须被安全插值进 data-apex-hash 属性选择器
        val ref = "r_3k9f"
        val js = BrowserScript.rectByRefJs(ref)
        // 新契约：ref 不再拼进 CSS 选择器，而是在 JS 内与属性值严格比较
        assertTrue(
            "应按 data-apex-hash 属性值定位：" + js,
            js.contains("getAttribute('data-apex-hash') === '$ref'"),
        )
        // 已知限制（基础设施层防护由 WebView 沙箱兜底）：ref 直接字符串插值，
        // 若含 `"` / `]` 可能闭合属性选择器；Agent 层传入的 ref 均来自快照注入的语义哈希，
        // 字符集受限，实际风险低。此处仅验证正常 ref 的嵌入契约。
    }

    // ---- 下拉选择：byText / byValue 两种匹配语义 ----

    @Test
    fun `selectJs byValue 按 option 的 value 匹配`() {
        val js = BrowserScript.selectJs("r_abc", "cn", byText = false)
        assertTrue("按 value 匹配", js.contains("opt.value"))
        assertTrue(js.contains("getAttribute('data-apex-hash') === 'r_abc'"))
        assertFalse("不应按 text 匹配", js.contains("opt.text"))
    }

    @Test
    fun `selectJs byText 按 option 的 text 匹配`() {
        val js = BrowserScript.selectJs("r_abc", "中国", byText = true)
        assertTrue("按 text 匹配", js.contains("opt.text"))
    }

    // ---- 网络监控：注入后拦截 fetch / xhr 并写入日志 ----

    @Test
    fun `NETWORK_MONITOR_JS 拦截 fetch 与 XMLHttpRequest 并写入日志`() {
        val js = BrowserScript.NETWORK_MONITOR_JS
        // 意图：window.__apexNetLog 是 browser_network_log 的数据源，必须被填充
        assertTrue(js.contains("window.__apexNetLog"))
        assertTrue(js.contains("window.fetch = function"))
        assertTrue(js.contains("XMLHttpRequest.prototype.open"))
        assertTrue(js.contains("XMLHttpRequest.prototype.send"))
        // 防止重复注入
        assertTrue(js.contains("if (window.__apexNetHooked) return"))
    }

    // ---- 点击后探针：返回 url/title/交互数供 Agent 判定导航是否成功 ----

    @Test
    fun `POST_ACTION_PROBE_JS 返回 url title 与交互元素数`() {
        val js = BrowserScript.POST_ACTION_PROBE_JS
        // 意图：点击验证必须能感知 URL / 标题变化与页面结构变化
        assertTrue(js.contains("url: location.href"))
        assertTrue(js.contains("title: document.title"))
        assertTrue(js.contains("interactiveCount:"))
    }

    // ---- 等待选择器：wait_for 参数支撑 ----

    @Test
    fun `waitForSelectorJs 把选择器作为 JS 字符串安全嵌入`() {
        val sel = "a.login"
        val js = BrowserScript.waitForSelectorJs(sel)
        // 意图：选择器被当作字面量查询，且带轮询 + 超时兜底
        assertTrue(js.contains("document.querySelector('$sel')"))
        assertTrue(js.contains("setInterval"))
        assertTrue(js.contains("setTimeout"))
    }

    // ---- 高亮：调试可视化 ----

    @Test
    fun `highlightJs 对目标 ref 注入 outline`() {
        val js = BrowserScript.highlightJs("r_z12", "#ff0000")
        // v1.1.0：ref 统一走单引号转义包装（'[data-apex-hash=\'r_z12\']'）
        assertTrue(js.contains("getAttribute('data-apex-hash') === 'r_z12'"))
        // 颜色也不再裸拼进引号字面量（同一类双重引号缺陷）
        assertTrue(js.contains("outline='2px solid ' + '#ff0000'"))
    }

    // ═══ v1.1.0：JS 字面量转义（错误率修复的核心防线） ═══

    @Test
    fun `ref 含单引号时选择器字面量不撕裂`() {
        // 语义哈希 ref 不含引号，但防御性转义必须就位——任何插值点被喂入
        // 恶意/意外 ref 都不能撕裂 JS（旧实现零转义，单引号直接语法报错）
        val js = BrowserScript.scrollIntoViewAndRectJs("r'x")
        // 生成物里不应出现裸的 'r'x' 撕裂形态：引号必须被转义为 \'
        assertTrue("单引号必须被转义", js.contains("\\'"))
        assertFalse("不应残留未转义的 ref 撕裂形态", js.contains("'r'x'"))
    }

    @Test
    fun `selector 含 CSS 属性选择器引号时 waitForSelectorJs 仍合法`() {
        // [href='login'] 是 Agent 高频形态；旧实现生成 querySelector('[href='login']') 必炸
        val sel = "[href='login']"
        val js = BrowserScript.waitForSelectorJs(sel)
        assertTrue("内层引号必须被转义", js.contains("\\'"))
        assertFalse(
            "不应出现撕裂的选择器字面量",
            js.contains("'$sel'") && !js.contains("\\'")
        )
    }

    @Test
    fun `selectJs 的选项值含撇号与换行不撕裂`() {
        val js = BrowserScript.selectJs("r_1", "it's\nmulti", byText = false)
        assertTrue(js.contains("\\'"))
        assertTrue(js.contains("\\n"))
    }

    @Test
    fun `setNativeValueJs 多行文本转义为 n 字面量`() {
        val js = BrowserScript.setNativeValueJs("r_1", "第一行\n第二行", append = false)
        // 意图：换行必须变成 \n 转义序列，而不是字面换行（撕裂 JS 字符串的旧病）
        assertFalse("生成的 JS 内不应有字面换行出现在字符串字面量中", js.contains("第一行\n第二行"))
        assertTrue(js.contains("\\n"))
    }

    @Test
    fun `setNativeValueJs 走原型链原生 setter 并派发事件`() {
        val js = BrowserScript.setNativeValueJs("r_1", "hello", append = false)
        // 意图：React/Vue 受控组件兼容——native setter + input/change 事件
        assertTrue(js.contains("getOwnPropertyDescriptor"))
        assertTrue(js.contains("desc.set.call"))
        assertTrue(js.contains("new Event('input'"))
        assertTrue(js.contains("new Event('change'"))
        assertTrue(js.contains("isContentEditable"))
    }

    @Test
    fun `setNativeValueJs append 模式拼接现值`() {
        val appendJs = BrowserScript.setNativeValueJs("r_1", "x", append = true)
        val replaceJs = BrowserScript.setNativeValueJs("r_1", "x", append = false)
        assertTrue("append 模式应拼接现值", appendJs.contains("(cur || '') +"))
        assertFalse("replace 模式不应拼接", replaceJs.contains("(cur || '') +"))
    }

    // ═══ v1.1.0：新动作空间脚本的意图 ═══

    @Test
    fun `scrollIntoViewAndRectJs 先滚动到中央再回读矩形`() {
        val js = BrowserScript.scrollIntoViewAndRectJs("r_1")
        assertTrue(js.contains("scrollIntoView"))
        assertTrue(js.contains("block:'center'"))
        assertTrue(js.contains("getBoundingClientRect"))
        assertTrue(js.contains("window.innerWidth"))
    }

    @Test
    fun `elementAtPointJs 做命中测试并判定目标归属`() {
        val js = BrowserScript.elementAtPointJs(120.5f, 300f, "r_1")
        assertTrue(js.contains("document.elementFromPoint(120.5, 300"))
        assertTrue(js.contains("isTargetOrChild"))
        assertTrue(js.contains("target.contains(el)"))
    }

    @Test
    fun `locateByFuzzyJs 命中后重打原 ref`() {
        val js = BrowserScript.locateByFuzzyJs("button", "提交订单", "r_abc")
        assertTrue(js.contains("indexOf(want)"))
        assertTrue(js.contains("bestLen")) // 最短文本 = 最精确匹配
        assertTrue(js.contains("setAttribute('data-apex-hash'"))
    }

    @Test
    fun `pressKeyJs 的 Enter 携带表单提交语义`() {
        val js = BrowserScript.pressKeyJs("enter")
        assertTrue(js.contains("keydown"))
        assertTrue(js.contains("keyup"))
        assertTrue(js.contains("requestSubmit"))
    }

    @Test
    fun `pressKeyJs 未知键按单字符派发`() {
        val js = BrowserScript.pressKeyJs("a")
        assertTrue(js.contains("keydown"))
        // 意图：键值如实下发为单字符（Enter 分支是模板常驻代码，由运行时键值门控）
        assertTrue("键值应按单字符下发", js.contains("var key = 'a'"))
        assertFalse("单字符键的键值不应是 Enter", js.contains("var key = 'Enter'"))
    }

    @Test
    fun `hoverJs 派发完整悬停事件序列`() {
        val js = BrowserScript.hoverJs("r_1")
        assertTrue(js.contains("mouseover"))
        assertTrue(js.contains("mouseenter"))
        assertTrue(js.contains("mousemove"))
    }

    @Test
    fun `extractContentJs 四模式意图齐全`() {
        for (mode in listOf("article", "tables", "links", "meta")) {
            val js = BrowserScript.extractContentJs(mode)
            assertTrue("模式 $mode 应如实下发", js.contains("var mode = '$mode'"))
        }
        // article 模式的正文抽取骨架
        val art = BrowserScript.extractContentJs("article")
        assertTrue(art.contains("querySelector('article')"))
        assertTrue(art.contains("wordCount"))
        // tables 模式行列封顶
        val tabs = BrowserScript.extractContentJs("tables")
        assertTrue(tabs.contains("rows.length<50"))
        assertTrue(tabs.contains("cells.length<30"))
        // links 模式去重
        val links = BrowserScript.extractContentJs("links")
        assertTrue(links.contains("seen[h]"))
        // meta 模式 og 标签
        val meta = BrowserScript.extractContentJs("meta")
        assertTrue(meta.contains("og:title"))
        assertTrue(meta.contains("canonical"))
    }

    // -- ref 定位：不得把 ref 拼进 CSS 选择器（安全 + 可用性）--

    @Test
    fun `ref 定位不得拼进 CSS 属性选择器`() {
        // 意图：旧形态 querySelector('[data-apex-hash=${ref.toJsonString()}]') 有两个同时存在的故障：
        //  (a) 模板自带单引号 + 转义函数又套一层单引号 -> 生成
        //      document.querySelector('[data-apex-hash=' + 'r_3k9f' + ']') 之外的形式
        //      document.querySelector('[data-apex-hash='r_3k9f']')，对**任何** ref 都是语法错误，
        //      体现为 click / input / select 完全无法工作；
        //  (b) 可注入 —— 转义虽已正确跳过引号，但注入载荷**不需要任何引号字符**即可脱离：
        //      ref = "+alert(document.cookie)+" -> document.querySelector('[data-apex-hash='+alert(document.cookie)+']')
        //      合法 JS，页内任意代码执行。
        // 故改为「取全部带标记元素 -> JS 内 === 比较属性值」。
        val js = BrowserScript.rectByRefJs("r_3k9f")
        assertFalse(
            "不得把 ref 拼进 CSS 属性选择器：" + js,
            js.contains("[data-apex-hash=" + "'" + " +"),
        )
        assertFalse(
            "不得保留双重引号形态：" + js,
            js.contains("'" + "[data-apex-hash="),
        )
        assertTrue(
            "应改为 querySelectorAll + 属性值比较：" + js,
            js.contains("getAttribute(" + "'" + "data-apex-hash" + "'" + ") ==="),
        )
    }

    @Test
    fun `七个 ref 定位入口全部改用属性值比较`() {
        // 意图：每个靠 ref 定位的注入点都必须同步，否则修了其中几处仍留一个可注入面。
        // 函数名由脚本从 main 的源码映射得出，与 7 个注入点一一对应。
        val jsSites = listOf(
            "rectByRefJs" to BrowserScript.rectByRefJs("r_3k9f"),
            "selectJs" to BrowserScript.selectJs("r_9z", "cn", byText = false),
            "highlightJs" to BrowserScript.highlightJs("r_hl"),
            "scrollIntoViewAndRectJs" to BrowserScript.scrollIntoViewAndRectJs("r_sv"),
            "elementAtPointJs" to BrowserScript.elementAtPointJs(100f, 200f, "r_ep"),
            "setNativeValueJs" to BrowserScript.setNativeValueJs("r_nv", "hi", false),
            "hoverJs" to BrowserScript.hoverJs("r_hv"),
        )
        assertEquals("ref 定位入口数量", 7, jsSites.size)
        for ((name, js) in jsSites) {
            assertFalse(
                name + " 仍把 ref 拼进 CSS 属性选择器",
                js.contains("[data-apex-hash=" + "'" + " +"),
            )
            assertFalse(
                name + " 仍保留双重引号形态",
                js.contains("'" + "[data-apex-hash="),
            )
            assertTrue(
                name + " 应通过 querySelectorAll + 属性值比较定位",
                js.contains("getAttribute(" + "'" + "data-apex-hash" + "'" + ") ==="),
            )
        }
    }

    @Test
    fun `定位片段声明统一的 __apexHits 与 __apexFirst`() {
        // 意图：多个注入点共用同一前置片段，变量名不能各处自定义（重名会静默抬高一个入口）。
        val js = BrowserScript.rectByRefJs("r_3k9f")
        assertTrue(js.contains("var __apexHits = [];"))
        assertTrue(js.contains("var __apexFirst = __apexHits.length ? __apexHits[0] : null;"))
        // 循环变量必须带前缀，避免与调用方脚本里的 i / el 碰撞
        assertTrue(js.contains("__apexI"))
    }


    @Test
    fun `highlightJs 的颜色不得闭合样式串`() {
        // 意图：旧孢态 outline='2px solid $color' 与 ref 定位同类：
        // 模板自带引号 + 变量未经转义。注入载荷不需引号字符即可脱离。
        val js = BrowserScript.highlightJs("r_z12", "red'; alert(1); //")
        assertFalse(
            "不得出现未转义的模板引号：" + js,
            js.contains("'2px solid red'"),
        )
        assertTrue(
            "颜色应经转义后拼接：" + js,
            js.contains("outline='2px solid ' + ") && js.contains("\\'"),
        )
    }
}

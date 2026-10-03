package com.apex.browser.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [PageClassifier] 的「意图」：让模型在抓全量快照**之前**先建立页面心智模型，
 * 从而选择正确的剪枝策略（表单页抓 FORM_FIELDS、文章页抓 CONTENT_SUMMARY）。
 *
 * 这些用例此前**完全没有覆盖** —— 分类逻辑是 `BrowserEngine` 里的 private 方法，
 * 与 WebView 耦合、无法在 JVM 测。下沉到 `:browser-core` 后才有了这一层。
 * 分类优先级表本身是契约：改动它等于改动 Agent 的 snapshot 策略。
 */
class PageClassifierTest {

    private fun classify(vararg pairs: Pair<String, Int>) =
        PageClassifier.classify(mapOf(*pairs))

    // ---- 优先级：认证最高（登录页也满足表单条件，必须先判为 auth）----

    @Test
    fun `存在密码框即判为认证页且优先于表单规则`() {
        // 意图：登录页通常有 2~3 个输入框 + 提交按钮，完全满足 form 规则(inputs>=4 不一定，
        // 但补足到 4 后必然双命中)。若 form 先命中，就会丢掉「凭据需人工确认」这一关键提示。
        val info = classify(
            "password" to 1, "inputs" to 5, "buttons" to 2, "links" to 30, "textLen" to 5000,
        )
        assertEquals("auth", info.type)
        assertTrue("认证页提示应提示凭据确认", info.hint.contains("凭据"))
    }

    @Test
    fun `密码框在视频页也优先判为认证页`() {
        // 会员视频站的「登录后观看」弹层含 password 框 —— 仍须按 auth 处理。
        val info = classify("password" to 1, "videos" to 3, "textLen" to 900)
        assertEquals("auth", info.type)
    }

    // ---- 视频页 ----

    @Test
    fun `有视频且正文较短判为视频页`() {
        val info = classify("videos" to 2, "textLen" to 1200, "links" to 5)
        assertEquals("video", info.type)
    }

    @Test
    fun `正文极长的含视频页不判为视频页`() {
        // 意图：textLen 门槛防止「长文章里嵌一个视频」被误判为播放页，
        // 那会让 Agent 跳过 CONTENT_SUMMARY 从而抓不到正文。
        val info = classify("videos" to 1, "textLen" to 9000, "articles" to 1)
        assertEquals("article", info.type)
    }

    // ---- 表单页 ----

    @Test
    fun `输入框充足且有按钮判为表单页`() {
        val info = classify("inputs" to 6, "buttons" to 1, "links" to 3, "textLen" to 600)
        assertEquals("form", info.type)
        assertTrue("表单页提示应指向 form 聚焦策略", info.hint.contains("focus=form"))
    }

    @Test
    fun `输入框充足但无按钮不判为表单页`() {
        // 无提交按钮的表单（如搜索结果页的筛选器组）不足以支撑「逐项填写」的心智模型。
        val info = classify("inputs" to 6, "buttons" to 0, "links" to 3)
        assertEquals("generic", info.type)
    }

    // ---- 文章页 ----

    @Test
    fun `有 article 语义且正文充足判为文章页`() {
        val info = classify("articles" to 1, "textLen" to 3000, "links" to 10)
        assertEquals("article", info.type)
        assertTrue("文章页提示应指向 content 策略", info.hint.contains("focus=content"))
    }

    @Test
    fun `有 article 语义但正文很短不足以判为文章页`() {
        val info = classify("articles" to 1, "textLen" to 200, "links" to 4)
        assertEquals("generic", info.type)
    }

    // ---- 搜索页 ----

    @Test
    fun `有搜索框且结果链接充足判为搜索页`() {
        val info = classify("searchBox" to 1, "links" to 25, "listItems" to 20, "textLen" to 900)
        assertEquals("search", info.type)
    }

    @Test
    fun `有搜索框但链接不足判为通用页`() {
        val info = classify("searchBox" to 1, "links" to 3, "textLen" to 900)
        assertEquals("generic", info.type)
    }

    // ---- 列表页 / 门户页 ----

    @Test
    fun `链接与列表项都很多判为列表页`() {
        val info = classify("links" to 40, "listItems" to 30, "textLen" to 1200)
        assertEquals("list", info.type)
    }

    @Test
    fun `有导航语义且链接较多判为门户页`() {
        val info = classify("nav" to 1, "links" to 15, "textLen" to 700)
        assertEquals("portal", info.type)
    }

    @Test
    fun `链接很多但无列表项无导航仍为通用页`() {
        // 避免任何「链接多就算列表/门户」的宽松判定 —— 分类必须可解释。
        val info = classify("links" to 40, "textLen" to 700)
        assertEquals("generic", info.type)
    }

    // ---- 退化路径：任何信号缺失都不得抛错 ----

    @Test
    fun `空信号表退化为通用页`() {
        val info = PageClassifier.classify(emptyMap())
        assertEquals("generic", info.type)
        assertTrue("退化时 signals 应为空表", info.signals.isEmpty())
    }

    @Test
    fun `signals 原样回传给消费方便于解释推断依据`() {
        val signals = mapOf("inputs" to 5, "buttons" to 1)
        val info = PageClassifier.classify(signals)
        assertEquals("signals 必须原样回传，Agent 需据此解释分类依据", signals, info.signals)
    }

    // ---- 信号 JSON 解析：防御式，任何畸形输入都不得抛错 ----

    @Test
    fun `解析真实形态的信号 JSON`() {
        val raw = """{"inputs":5,"password":0,"buttons":2,"links":12,"textLen":3200}"""
        val signals = PageClassifier.parseSignals(raw)
        assertEquals(5, signals["inputs"])
        assertEquals(0, signals["password"])
        assertEquals(3200, signals["textLen"])
    }

    @Test
    fun `畸形与空输入一律降级为空表而非抛错`() {
        // 意图：页面可能被 CSP 拦截或未加载完，此时 JS 可能返回 null/非对象/截断串。
        // 工具应退化为 generic 继续工作，而不是让 browser_page_type 整体失败。
        for (raw in listOf("", "null", "{}", "not-json", "[1,2,3]", """{"inputs":"x"}""", "\u0000")) {
            assertEquals(
                "畸形输入应返回空表: '$raw'",
                emptyMap<String, Int>(),
                PageClassifier.parseSignals(raw),
            )
        }
    }

    @Test
    fun `非整数值的键被跳过而不影响其余键`() {
        val signals = PageClassifier.parseSignals("""{"inputs":"abc","buttons":2}""")
        assertEquals(2, signals["buttons"])
        assertTrue("非整数键应被跳过", signals.keys.none { it == "inputs" })
    }

    @Test
    fun `fromJson 一步完成解析与分类`() {
        val raw = """{"password":1,"inputs":5,"buttons":2}"""
        assertEquals("auth", PageClassifier.fromJson(raw).type)
        // 畸形输入同样安全
        assertEquals("generic", PageClassifier.fromJson("garbage").type)
    }

    // ---- 与 JS 采集面的契约：字段名必须对齐 ----

    @Test
    fun `PAGE_TYPE_JS 采集的字段名与分类器读取的键一一对应`() {
        // 意图：JS 侧改名而 Kotlin 侧未改是最隐蔽的漂移 —— 分类会静默退化为 generic，
        // 没有任何报错。这里把「JS 产出的键」与「分类器读取的键」显式锁在一起。
        val js = BrowserScript.PAGE_TYPE_JS
        val expected = listOf(
            "inputs", "password", "buttons", "links", "articles",
            "videos", "listItems", "searchBox", "nav", "textLen",
        )
        for (key in expected) {
            assertTrue("PAGE_TYPE_JS 应采集信号 $key", js.contains("$key: q(") || js.contains("$key: b ?"))
        }
    }

    @Test
    fun `PAGE_TYPE_JS 必须是同步返回而非 Promise`() {
        // 意图：evaluateJavascript 不等待 Promise，异步形态拿到的恒为 null，
        // 信号采集会静默失效（browser_page_type 永远 generic）。
        val js = BrowserScript.PAGE_TYPE_JS
        assertTrue("不得含 Promise", !js.contains("new Promise"))
        assertTrue("应直接 JSON.stringify 返回", js.contains("JSON.stringify"))
    }
}
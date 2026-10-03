package com.apex.browser.core

/**
 * 页面形态信号 → 页面类型 + 动作建议。
 *
 * ## 为什么从引擎下沉到 core
 *
 * 信号采集是一次 JS（见 [BrowserScript.pageTypeJs]），而**分类本身是纯 Kotlin 逻辑**：
 * 原实现把它与 `BrowserEngine` 绑在一起且全部 `private`，导致
 * - 分类优先级表（auth > video > form > article > search > list > portal）**零测试覆盖**——
 *   而这正是最容易被后续改动悄悄改坏的部分；
 * - 下游消费方即便只想复用「按信号分类」也必须拖上整个 Android 引擎。
 *
 * 下沉到零 Android 依赖的 `:browser-core` 后，分类策略可在纯 JVM 单测中穷举验证
 * （见 `PageClassifierTest`），并对任何消费方开放。
 */
object PageClassifier {

    /** 分类结果：类型 + 该类型的典型动作建议 + 原始信号（供 Agent 解释推断依据）。 */
    data class PageTypeInfo(
        val type: String,
        val hint: String,
        val signals: Map<String, Int>,
    )

    /**
     * 防御式解析 [BrowserScript.pageTypeJs] 回传的信号 JSON。
     *
     * 任何形状异常（空串 / `null` / 非对象 / 值不是整数）都降级为「该键缺失」而非抛错 ——
     * 页面可能被 CSP 拦截或尚未加载，分类退化到 [generic] 即可，不该让工具整体失败。
     */
    fun parseSignals(rawJson: String): Map<String, Int> {
        if (rawJson.isEmpty() || rawJson == "null") return emptyMap()
        val element = runCatching { kotlinx.serialization.json.Json.parseToJsonElement(rawJson) }
            .getOrNull() ?: return emptyMap()
        val obj = element as? kotlinx.serialization.json.JsonObject ?: return emptyMap()
        val out = LinkedHashMap<String, Int>(obj.size)
        for ((key, value) in obj) {
            val primitive = value as? kotlinx.serialization.json.JsonPrimitive ?: continue
            val parsed = runCatching { primitive.content.toInt() }.getOrNull() ?: continue
            out[key] = parsed
        }
        return out
    }

    /**
     * 信号 → 页面类型。
     *
     * 优先级从高到低：**认证 > 视频 > 表单 > 文章 > 搜索 > 列表 > 门户 > 通用**。
     * 顺序本身是契约 —— 例如登录页通常也有多个输入框，若表单规则先命中，
     * 就会被判成 `form` 而错过「凭据类操作需人工确认」这一关键提示。
     */
    fun classify(signals: Map<String, Int>): PageTypeInfo {
        val inputs = signals["inputs"] ?: 0
        val password = signals["password"] ?: 0
        val buttons = signals["buttons"] ?: 0
        val links = signals["links"] ?: 0
        val articles = signals["articles"] ?: 0
        val videos = signals["videos"] ?: 0
        val listItems = signals["listItems"] ?: 0
        val searchBox = signals["searchBox"] ?: 0
        val nav = signals["nav"] ?: 0
        val textLen = signals["textLen"] ?: 0
        return when {
            password > 0 -> PageTypeInfo(
                "auth", "登录/注册页：先 browser_snapshot(focus=form) 找输入框，凭据类操作注意确认", signals
            )
            videos > 0 && textLen < 6000 -> PageTypeInfo(
                "video", "视频/播放页：控件多为自定义 DOM，建议 browser_snapshot 后按 ref 点击", signals
            )
            inputs >= 4 && buttons >= 1 -> PageTypeInfo(
                "form", "表单页：browser_snapshot(focus=form) 拿全字段，逐项 browser_input/select/date_input", signals
            )
            articles > 0 && textLen > 1500 -> PageTypeInfo(
                "article", "文章/详情页：browser_snapshot(focus=content) 抓正文；交互元素通常在评论区", signals
            )
            searchBox > 0 && links >= 8 -> PageTypeInfo(
                "search", "搜索/结果页：可在搜索框继续输入，结果项用列表 ref 定位", signals
            )
            links >= 20 && listItems >= 15 -> PageTypeInfo(
                "list", "列表/信息流页：元素多且分页，建议 focus 策略 + scroll 翻页", signals
            )
            nav > 0 && links >= 10 -> PageTypeInfo(
                "portal", "门户/首页：导航入口为主，先想清楚目标路径再点击", signals
            )
            else -> PageTypeInfo(
                "generic", "普通页面：browser_snapshot 全量观察后再决策", signals
            )
        }
    }

    /** 便捷入口：解析 + 分类一步完成。 */
    fun fromJson(rawJson: String): PageTypeInfo = classify(parseSignals(rawJson))
}